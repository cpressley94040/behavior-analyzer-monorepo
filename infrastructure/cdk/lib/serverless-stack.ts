import * as cdk from 'aws-cdk-lib';
import * as apigateway from 'aws-cdk-lib/aws-apigateway';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as s3 from 'aws-cdk-lib/aws-s3';
import { Construct } from 'constructs';
import * as path from 'path';

export interface ServerlessStackProps extends cdk.StackProps {
  /**
   * Environment name (e.g., 'dev', 'prod')
   */
  environment?: string;

  /**
   * Enable detailed CloudWatch metrics
   */
  enableDetailedMetrics?: boolean;

  /**
   * Lambda memory size in MB (default: 512)
   */
  lambdaMemorySize?: number;

  /**
   * Lambda timeout in seconds (default: 30)
   */
  lambdaTimeout?: number;

  /**
   * Event TTL in days for DynamoDB (default: 90)
   */
  eventTtlDays?: number;

  /**
   * Accuracy threshold to consider an event "interesting" (default: 0.7 = 70%)
   */
  accuracyInterestingThreshold?: number;

  /**
   * Headshot ratio threshold to consider an event "interesting" (default: 0.5 = 50%)
   */
  headshotInterestingThreshold?: number;

  /**
   * Z-score threshold for anomaly detection (default: 3.0)
   */
  zscoreThreshold?: number;

  /**
   * API Gateway rate limit in requests per second (default: 100)
   * Increased from 50 to support ~2,500 concurrent players
   */
  apiRateLimit?: number;

  /**
   * API Gateway burst limit for concurrent requests (default: 200)
   * Increased from 100 to handle traffic spikes
   */
  apiBurstLimit?: number;

  /**
   * API Gateway daily request quota (default: 5000000)
   * Increased from 1M to support ~500 concurrent players
   */
  apiDailyQuota?: number;
}

/**
 * Serverless Stack - Lambda-Only Infrastructure for cost-optimized deployments.
 *
 * This stack provides a fully serverless architecture optimized for single game server
 * deployments with up to 500 concurrent players. It trades some real-time capability
 * for significantly reduced operational costs.
 *
 * @remarks
 * **Architecture:**
 * ```
 * Rust Plugin → API Gateway → Lambda → DynamoDB
 *                                ↓
 *                               S3 (models)
 * ```
 *
 * **Cost Optimization Strategy:**
 * - No VPC (reduces NAT Gateway costs)
 * - No ElastiCache (Lambda handles stateless processing)
 * - No Kinesis (API Gateway handles event batching)
 * - Smart event filtering: Only stores "interesting" events
 *
 * **Estimated cost:** $50-100/month for typical usage
 *
 * **Event Storage Policy:**
 * - Always stored: SESSION_START, SESSION_END, PLAYER_KILLED, PLAYER_REPORTED, PLAYER_VIOLATION
 * - Conditionally stored: Events with high accuracy (>70%) or headshot ratio (>50%)
 * - Never stored: Routine PLAYER_TICK, PLAYER_INPUT, looting events (but stats are updated)
 *
 * @example
 * ```typescript
 * const serverlessStack = new ServerlessStack(app, 'BehaviorAnalyzerServerless', {
 *   environment: 'prod',
 *   lambdaMemorySize: 512,
 *   accuracyInterestingThreshold: 0.7,
 *   zscoreThreshold: 3.0,
 * });
 * ```
 */
export class ServerlessStack extends cdk.Stack {
  public readonly api: apigateway.RestApi;
  public readonly eventsTable: dynamodb.Table;
  public readonly playerStateTable: dynamodb.Table;
  public readonly detectionsTable: dynamodb.Table;
  public readonly modelBucket: s3.Bucket;
  public readonly processorFunction: lambda.Function;

  constructor(scope: Construct, id: string, props: ServerlessStackProps = {}) {
    super(scope, id, props);

    const environment = props.environment || 'dev';
    const lambdaMemorySize = props.lambdaMemorySize || 512;
    const lambdaTimeout = props.lambdaTimeout || 30;
    const eventTtlDays = props.eventTtlDays || 90;
    const accuracyThreshold = props.accuracyInterestingThreshold || 0.7;
    const headshotThreshold = props.headshotInterestingThreshold || 0.5;
    const zscoreThreshold = props.zscoreThreshold || 3.0;
    const apiRateLimit = props.apiRateLimit || 100;    // Increased from 50 to support more concurrent players
    const apiBurstLimit = props.apiBurstLimit || 200;  // Increased from 100 for better spike handling
    const apiDailyQuota = props.apiDailyQuota || 5000000; // Increased from 1M to 5M

    // =========================================================================
    // DynamoDB Tables
    // =========================================================================

    // Events Table - stores raw telemetry events
    this.eventsTable = new dynamodb.Table(this, 'EventsTable', {
      tableName: `behavior-analyzer-events-${environment}`,
      partitionKey: { name: 'pk', type: dynamodb.AttributeType.STRING }, // owner#playerId
      sortKey: { name: 'sk', type: dynamodb.AttributeType.STRING },      // timestamp#eventId
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      timeToLiveAttribute: 'ttl',
      pointInTimeRecoverySpecification: { pointInTimeRecoveryEnabled: true },
      removalPolicy: cdk.RemovalPolicy.RETAIN,
    });

    // GSI for querying by action type (for analysis)
    this.eventsTable.addGlobalSecondaryIndex({
      indexName: 'action-type-index',
      partitionKey: { name: 'actionType', type: dynamodb.AttributeType.STRING },
      sortKey: { name: 'timestamp', type: dynamodb.AttributeType.NUMBER },
      projectionType: dynamodb.ProjectionType.KEYS_ONLY,
    });

    // Player State Table - stores player profiles and feature vectors
    this.playerStateTable = new dynamodb.Table(this, 'PlayerStateTable', {
      tableName: `behavior-analyzer-players-${environment}`,
      partitionKey: { name: 'pk', type: dynamodb.AttributeType.STRING }, // owner#playerId
      sortKey: { name: 'sk', type: dynamodb.AttributeType.STRING },      // 'PROFILE' | 'FEATURES' | 'SESSION#id'
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      pointInTimeRecoverySpecification: { pointInTimeRecoveryEnabled: true },
      removalPolicy: cdk.RemovalPolicy.RETAIN,
    });

    // GSI for querying by risk score (for dashboard)
    this.playerStateTable.addGlobalSecondaryIndex({
      indexName: 'risk-score-index',
      partitionKey: { name: 'owner', type: dynamodb.AttributeType.STRING },
      sortKey: { name: 'riskScore', type: dynamodb.AttributeType.NUMBER },
      projectionType: dynamodb.ProjectionType.ALL,
    });

    // Detections Table - stores anomaly detections and flags
    this.detectionsTable = new dynamodb.Table(this, 'DetectionsTable', {
      tableName: `behavior-analyzer-detections-${environment}`,
      partitionKey: { name: 'pk', type: dynamodb.AttributeType.STRING }, // owner#playerId
      sortKey: { name: 'sk', type: dynamodb.AttributeType.STRING },      // timestamp#detectionId
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      timeToLiveAttribute: 'ttl',
      pointInTimeRecoverySpecification: { pointInTimeRecoveryEnabled: true },
      removalPolicy: cdk.RemovalPolicy.RETAIN,
    });

    // GSI for querying unresolved detections
    this.detectionsTable.addGlobalSecondaryIndex({
      indexName: 'status-index',
      partitionKey: { name: 'owner', type: dynamodb.AttributeType.STRING },
      sortKey: { name: 'status', type: dynamodb.AttributeType.STRING },
      projectionType: dynamodb.ProjectionType.ALL,
    });

    // =========================================================================
    // S3 Bucket for Models
    // =========================================================================

    this.modelBucket = new s3.Bucket(this, 'ModelBucket', {
      bucketName: `behavior-analyzer-models-${this.account}-${environment}`,
      versioned: true,
      encryption: s3.BucketEncryption.S3_MANAGED,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      removalPolicy: cdk.RemovalPolicy.RETAIN,
      lifecycleRules: [
        {
          id: 'DeleteOldVersions',
          noncurrentVersionExpiration: cdk.Duration.days(30),
          enabled: true,
        },
      ],
    });

    // =========================================================================
    // Lambda Function for Event Processing
    // =========================================================================

    // Lambda execution role
    const lambdaRole = new iam.Role(this, 'ProcessorRole', {
      assumedBy: new iam.ServicePrincipal('lambda.amazonaws.com'),
      managedPolicies: [
        iam.ManagedPolicy.fromAwsManagedPolicyName('service-role/AWSLambdaBasicExecutionRole'),
      ],
    });

    // Grant DynamoDB access
    this.eventsTable.grantReadWriteData(lambdaRole);
    this.playerStateTable.grantReadWriteData(lambdaRole);
    this.detectionsTable.grantReadWriteData(lambdaRole);

    // Grant S3 read access for models
    this.modelBucket.grantRead(lambdaRole);

    // Create log group for processor Lambda (using logGroup instead of deprecated logRetention)
    const processorLogGroup = new logs.LogGroup(this, 'ProcessorLogGroup', {
      logGroupName: `/aws/lambda/behavior-analyzer-processor-${environment}`,
      retention: logs.RetentionDays.TWO_WEEKS,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    // Processor Lambda
    this.processorFunction = new lambda.Function(this, 'ProcessorFunction', {
      functionName: `behavior-analyzer-processor-${environment}`,
      runtime: lambda.Runtime.PYTHON_3_11,
      handler: 'handler.lambda_handler',
      code: lambda.Code.fromAsset(path.join(__dirname, '../lambda/processor')),
      memorySize: lambdaMemorySize,
      timeout: cdk.Duration.seconds(lambdaTimeout),
      role: lambdaRole,
      environment: {
        EVENTS_TABLE: this.eventsTable.tableName,
        PLAYER_STATE_TABLE: this.playerStateTable.tableName,
        DETECTIONS_TABLE: this.detectionsTable.tableName,
        MODEL_BUCKET: this.modelBucket.bucketName,
        EVENT_TTL_DAYS: eventTtlDays.toString(),
        ENVIRONMENT: environment,
        // Interesting event thresholds
        ACCURACY_INTERESTING_THRESHOLD: accuracyThreshold.toString(),
        HEADSHOT_INTERESTING_THRESHOLD: headshotThreshold.toString(),
        ZSCORE_THRESHOLD: zscoreThreshold.toString(),
      },
      logGroup: processorLogGroup,
      tracing: lambda.Tracing.ACTIVE,
    });

    // =========================================================================
    // API Gateway
    // =========================================================================

    this.api = new apigateway.RestApi(this, 'IngestApi', {
      restApiName: `behavior-analyzer-api-${environment}`,
      description: 'Behavior Analyzer Event Ingestion API',
      deployOptions: {
        stageName: environment,
        throttlingBurstLimit: apiBurstLimit,  // Max concurrent requests
        throttlingRateLimit: apiRateLimit,    // Requests per second
        metricsEnabled: props.enableDetailedMetrics || false,
        loggingLevel: apigateway.MethodLoggingLevel.INFO,
      },
      defaultCorsPreflightOptions: {
        allowOrigins: apigateway.Cors.ALL_ORIGINS,
        allowMethods: ['POST', 'OPTIONS'],
        allowHeaders: ['Content-Type', 'Authorization', 'X-Server-Key'],
      },
    });

    // API Key for authentication
    const apiKey = this.api.addApiKey('ServerApiKey', {
      apiKeyName: `behavior-analyzer-key-${environment}`,
      description: 'API key for Rust server plugin',
    });

    // Usage plan with rate limiting
    // Daily quota increased to 5M to support ~500 concurrent players with optimized batching
    const usagePlan = this.api.addUsagePlan('UsagePlan', {
      name: `behavior-analyzer-usage-${environment}`,
      throttle: {
        rateLimit: apiRateLimit,      // Requests per second (default: 50)
        burstLimit: apiBurstLimit,    // Concurrent requests (default: 100)
      },
      quota: {
        limit: apiDailyQuota,         // Daily request limit (default: 5M, was 1M)
        period: apigateway.Period.DAY,
      },
    });

    usagePlan.addApiKey(apiKey);
    usagePlan.addApiStage({
      stage: this.api.deploymentStage,
    });

    // /ingest endpoint
    const ingestResource = this.api.root.addResource('ingest');
    ingestResource.addMethod('POST', new apigateway.LambdaIntegration(this.processorFunction, {
      requestTemplates: {
        'application/json': '{ "body": $input.json("$"), "headers": { "Authorization": "$input.params(\'Authorization\')", "X-Server-Key": "$input.params(\'X-Server-Key\')" } }',
      },
    }), {
      apiKeyRequired: true,
    });

    // /health endpoint (no auth required)
    const healthResource = this.api.root.addResource('health');
    healthResource.addMethod('GET', new apigateway.MockIntegration({
      integrationResponses: [{
        statusCode: '200',
        responseTemplates: {
          'application/json': '{"status": "healthy", "version": "1.0.0"}',
        },
      }],
      requestTemplates: {
        'application/json': '{"statusCode": 200}',
      },
    }), {
      methodResponses: [{ statusCode: '200' }],
    });

    // =========================================================================
    // Outputs
    // =========================================================================

    new cdk.CfnOutput(this, 'ApiEndpoint', {
      value: this.api.url,
      description: 'API Gateway endpoint URL',
      exportName: `BehaviorAnalyzerApiEndpoint-${environment}`,
    });

    new cdk.CfnOutput(this, 'ApiKeyId', {
      value: apiKey.keyId,
      description: 'API Key ID (retrieve value from AWS Console)',
      exportName: `BehaviorAnalyzerApiKeyId-${environment}`,
    });

    new cdk.CfnOutput(this, 'EventsTableName', {
      value: this.eventsTable.tableName,
      description: 'Events DynamoDB Table',
      exportName: `BehaviorAnalyzerEventsTable-${environment}`,
    });

    new cdk.CfnOutput(this, 'PlayerStateTableName', {
      value: this.playerStateTable.tableName,
      description: 'Player State DynamoDB Table',
      exportName: `BehaviorAnalyzerPlayerStateTable-${environment}`,
    });

    new cdk.CfnOutput(this, 'DetectionsTableName', {
      value: this.detectionsTable.tableName,
      description: 'Detections DynamoDB Table',
      exportName: `BehaviorAnalyzerDetectionsTable-${environment}`,
    });

    new cdk.CfnOutput(this, 'ModelBucketName', {
      value: this.modelBucket.bucketName,
      description: 'Model S3 Bucket',
      exportName: `BehaviorAnalyzerModelBucket-${environment}`,
    });

    new cdk.CfnOutput(this, 'ProcessorFunctionArn', {
      value: this.processorFunction.functionArn,
      description: 'Processor Lambda ARN',
      exportName: `BehaviorAnalyzerProcessorArn-${environment}`,
    });
  }
}
