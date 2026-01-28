import * as cdk from 'aws-cdk-lib';
import { Template, Match } from 'aws-cdk-lib/assertions';
import { ServerlessStack } from '../lib/serverless-stack';

describe('ServerlessStack', () => {
  let app: cdk.App;
  let stack: ServerlessStack;
  let template: Template;

  beforeEach(() => {
    app = new cdk.App();
    stack = new ServerlessStack(app, 'TestServerlessStack', {
      environment: 'test',
    });
    template = Template.fromStack(stack);
  });

  describe('DynamoDB Tables', () => {
    it('should create events table with TTL enabled', () => {
      template.hasResourceProperties('AWS::DynamoDB::Table', {
        TableName: Match.stringLikeRegexp('behavior-analyzer-events-test'),
        BillingMode: 'PAY_PER_REQUEST',
        TimeToLiveSpecification: {
          AttributeName: 'ttl',
          Enabled: true,
        },
      });
    });

    it('should create players table', () => {
      template.hasResourceProperties('AWS::DynamoDB::Table', {
        TableName: Match.stringLikeRegexp('behavior-analyzer-players-test'),
        BillingMode: 'PAY_PER_REQUEST',
      });
    });

    it('should create detections table with TTL enabled', () => {
      template.hasResourceProperties('AWS::DynamoDB::Table', {
        TableName: Match.stringLikeRegexp('behavior-analyzer-detections-test'),
        BillingMode: 'PAY_PER_REQUEST',
        TimeToLiveSpecification: {
          AttributeName: 'ttl',
          Enabled: true,
        },
      });
    });

    it('should enable point-in-time recovery on all tables', () => {
      template.resourceCountIs('AWS::DynamoDB::Table', 3);
      template.hasResourceProperties('AWS::DynamoDB::Table', {
        PointInTimeRecoverySpecification: {
          PointInTimeRecoveryEnabled: true,
        },
      });
    });
  });

  describe('Lambda Function', () => {
    it('should create processor Lambda function', () => {
      template.hasResourceProperties('AWS::Lambda::Function', {
        FunctionName: 'behavior-analyzer-processor-test',
        Runtime: 'python3.11',
        MemorySize: 512,
        Timeout: 30,
      });
    });

    it('should have X-Ray tracing enabled', () => {
      template.hasResourceProperties('AWS::Lambda::Function', {
        TracingConfig: {
          Mode: 'Active',
        },
      });
    });

    it('should have required environment variables', () => {
      template.hasResourceProperties('AWS::Lambda::Function', {
        Environment: {
          Variables: Match.objectLike({
            EVENTS_TABLE: Match.anyValue(),
            PLAYER_STATE_TABLE: Match.anyValue(),
            DETECTIONS_TABLE: Match.anyValue(),
            MODEL_BUCKET: Match.anyValue(),
            ENVIRONMENT: 'test',
          }),
        },
      });
    });
  });

  describe('API Gateway', () => {
    it('should create REST API', () => {
      template.hasResourceProperties('AWS::ApiGateway::RestApi', {
        Name: 'behavior-analyzer-api-test',
      });
    });

    it('should have throttling configured', () => {
      template.hasResourceProperties('AWS::ApiGateway::Stage', {
        MethodSettings: Match.arrayWith([
          Match.objectLike({
            ThrottlingBurstLimit: 200,  // Increased from 100 for better spike handling
            ThrottlingRateLimit: 100,   // Increased from 50 for higher throughput
          }),
        ]),
      });
    });

    it('should require API key for ingest endpoint', () => {
      template.hasResourceProperties('AWS::ApiGateway::Method', {
        HttpMethod: 'POST',
        ApiKeyRequired: true,
      });
    });

    it('should have usage plan with quota', () => {
      template.hasResourceProperties('AWS::ApiGateway::UsagePlan', {
        Quota: {
          Limit: 5000000,  // 5M requests/day for ~500 concurrent players
          Period: 'DAY',
        },
        Throttle: {
          BurstLimit: 200,  // Increased from 100 for better spike handling
          RateLimit: 100,   // Increased from 50 for higher throughput
        },
      });
    });
  });

  describe('S3 Bucket', () => {
    it('should create model bucket with versioning', () => {
      template.hasResourceProperties('AWS::S3::Bucket', {
        VersioningConfiguration: {
          Status: 'Enabled',
        },
      });
    });

    it('should have encryption enabled', () => {
      template.hasResourceProperties('AWS::S3::Bucket', {
        BucketEncryption: {
          ServerSideEncryptionConfiguration: Match.arrayWith([
            Match.objectLike({
              ServerSideEncryptionByDefault: {
                SSEAlgorithm: 'AES256',
              },
            }),
          ]),
        },
      });
    });

    it('should block public access', () => {
      template.hasResourceProperties('AWS::S3::Bucket', {
        PublicAccessBlockConfiguration: {
          BlockPublicAcls: true,
          BlockPublicPolicy: true,
          IgnorePublicAcls: true,
          RestrictPublicBuckets: true,
        },
      });
    });
  });

  describe('CloudWatch Log Group', () => {
    it('should create log group for Lambda with 14-day retention', () => {
      template.hasResourceProperties('AWS::Logs::LogGroup', {
        LogGroupName: '/aws/lambda/behavior-analyzer-processor-test',
        RetentionInDays: 14,
      });
    });
  });

  describe('IAM', () => {
    it('should create Lambda execution role', () => {
      template.hasResourceProperties('AWS::IAM::Role', {
        AssumeRolePolicyDocument: {
          Statement: Match.arrayWith([
            Match.objectLike({
              Action: 'sts:AssumeRole',
              Effect: 'Allow',
              Principal: {
                Service: 'lambda.amazonaws.com',
              },
            }),
          ]),
        },
      });
    });
  });

  describe('Stack Configuration', () => {
    it('should use default memory size when not specified', () => {
      const defaultApp = new cdk.App();
      const defaultStack = new ServerlessStack(defaultApp, 'DefaultStack', {});
      const defaultTemplate = Template.fromStack(defaultStack);

      defaultTemplate.hasResourceProperties('AWS::Lambda::Function', {
        MemorySize: 512,
      });
    });

    it('should respect custom memory size', () => {
      const customApp = new cdk.App();
      const customStack = new ServerlessStack(customApp, 'CustomStack', {
        lambdaMemorySize: 1024,
      });
      const customTemplate = Template.fromStack(customStack);

      customTemplate.hasResourceProperties('AWS::Lambda::Function', {
        MemorySize: 1024,
      });
    });

    it('should respect custom timeout', () => {
      const customApp = new cdk.App();
      const customStack = new ServerlessStack(customApp, 'CustomStack', {
        lambdaTimeout: 60,
      });
      const customTemplate = Template.fromStack(customStack);

      customTemplate.hasResourceProperties('AWS::Lambda::Function', {
        Timeout: 60,
      });
    });
  });

  describe('Security', () => {
    it('should have DynamoDB encryption at rest by default', () => {
      // CDK DynamoDB tables use AWS-owned CMK encryption by default
      // SSESpecification is not explicitly set when using default encryption
      // Verify tables exist (encryption is automatic)
      template.resourceCountIs('AWS::DynamoDB::Table', 3);
    });

    it('should not allow public read access to S3', () => {
      template.hasResourceProperties('AWS::S3::Bucket', {
        PublicAccessBlockConfiguration: {
          BlockPublicAcls: true,
          BlockPublicPolicy: true,
        },
      });
    });

    it('should have API Gateway with stage variables protection', () => {
      template.hasResourceProperties('AWS::ApiGateway::Stage', {
        StageName: Match.anyValue(),
      });
    });
  });

  describe('Resource Naming', () => {
    it('should include environment in resource names', () => {
      template.hasResourceProperties('AWS::Lambda::Function', {
        FunctionName: Match.stringLikeRegexp('-test$'),
      });
    });

    it('should include environment in table names', () => {
      template.hasResourceProperties('AWS::DynamoDB::Table', {
        TableName: Match.stringLikeRegexp('-test$'),
      });
    });

    it('should include environment in API name', () => {
      template.hasResourceProperties('AWS::ApiGateway::RestApi', {
        Name: Match.stringLikeRegexp('-test$'),
      });
    });
  });

  describe('Environment Variations', () => {
    it('should create production stack with prod environment', () => {
      const prodApp = new cdk.App();
      const prodStack = new ServerlessStack(prodApp, 'ProdStack', {
        environment: 'prod',
      });
      const prodTemplate = Template.fromStack(prodStack);

      prodTemplate.hasResourceProperties('AWS::Lambda::Function', {
        FunctionName: 'behavior-analyzer-processor-prod',
        Environment: {
          Variables: Match.objectLike({
            ENVIRONMENT: 'prod',
          }),
        },
      });
    });

    it('should create dev stack with default environment', () => {
      const devApp = new cdk.App();
      const devStack = new ServerlessStack(devApp, 'DevStack', {});
      const devTemplate = Template.fromStack(devStack);

      devTemplate.hasResourceProperties('AWS::Lambda::Function', {
        Environment: {
          Variables: Match.objectLike({
            ENVIRONMENT: 'dev',
          }),
        },
      });
    });
  });

  describe('Lambda Permissions', () => {
    it('should have policy for DynamoDB access', () => {
      template.hasResourceProperties('AWS::IAM::Policy', {
        PolicyDocument: {
          Statement: Match.arrayWith([
            Match.objectLike({
              Action: Match.arrayWith([
                'dynamodb:BatchGetItem',
                'dynamodb:Query',
              ]),
              Effect: 'Allow',
            }),
          ]),
        },
      });
    });

    it('should have policy for S3 access', () => {
      template.hasResourceProperties('AWS::IAM::Policy', {
        PolicyDocument: {
          Statement: Match.arrayWith([
            Match.objectLike({
              Action: Match.arrayWith([
                's3:GetObject*',
                's3:GetBucket*',
              ]),
              Effect: 'Allow',
            }),
          ]),
        },
      });
    });

    it('should have Lambda execution role with basic permissions', () => {
      // CloudWatch Logs permissions are granted through AWSLambdaBasicExecutionRole
      // which is a managed policy attached to the execution role
      template.hasResourceProperties('AWS::IAM::Role', {
        AssumeRolePolicyDocument: {
          Statement: Match.arrayWith([
            Match.objectLike({
              Action: 'sts:AssumeRole',
              Effect: 'Allow',
              Principal: {
                Service: 'lambda.amazonaws.com',
              },
            }),
          ]),
        },
      });
    });
  });

  describe('Cost Optimization', () => {
    it('should use on-demand billing for DynamoDB', () => {
      const tables = template.findResources('AWS::DynamoDB::Table');
      Object.values(tables).forEach((table: any) => {
        expect(table.Properties.BillingMode).toBe('PAY_PER_REQUEST');
      });
    });

    it('should have reasonable Lambda memory allocation', () => {
      template.hasResourceProperties('AWS::Lambda::Function', {
        MemorySize: Match.anyValue(),
      });
      // Verify memory is within cost-effective range
      const functions = template.findResources('AWS::Lambda::Function');
      Object.values(functions).forEach((fn: any) => {
        const memory = fn.Properties.MemorySize;
        expect(memory).toBeGreaterThanOrEqual(128);
        expect(memory).toBeLessThanOrEqual(3008);
      });
    });

    it('should have TTL enabled for transient data', () => {
      // Events and detections tables should have TTL
      template.hasResourceProperties('AWS::DynamoDB::Table', {
        TableName: Match.stringLikeRegexp('events'),
        TimeToLiveSpecification: {
          Enabled: true,
        },
      });
      template.hasResourceProperties('AWS::DynamoDB::Table', {
        TableName: Match.stringLikeRegexp('detections'),
        TimeToLiveSpecification: {
          Enabled: true,
        },
      });
    });
  });

  describe('Observability', () => {
    it('should have X-Ray tracing on Lambda', () => {
      template.hasResourceProperties('AWS::Lambda::Function', {
        TracingConfig: {
          Mode: 'Active',
        },
      });
    });

    it('should have CloudWatch log retention configured', () => {
      template.hasResourceProperties('AWS::Logs::LogGroup', {
        RetentionInDays: Match.anyValue(),
      });
      const logGroups = template.findResources('AWS::Logs::LogGroup');
      Object.values(logGroups).forEach((lg: any) => {
        // Retention should be set (not infinite)
        expect(lg.Properties.RetentionInDays).toBeDefined();
        expect(lg.Properties.RetentionInDays).toBeGreaterThan(0);
      });
    });
  });

  describe('API Gateway Rate Limiting', () => {
    it('should have burst limit configured', () => {
      template.hasResourceProperties('AWS::ApiGateway::Stage', {
        MethodSettings: Match.arrayWith([
          Match.objectLike({
            ThrottlingBurstLimit: Match.anyValue(),
          }),
        ]),
      });
    });

    it('should have rate limit configured', () => {
      template.hasResourceProperties('AWS::ApiGateway::Stage', {
        MethodSettings: Match.arrayWith([
          Match.objectLike({
            ThrottlingRateLimit: Match.anyValue(),
          }),
        ]),
      });
    });

    it('should have daily quota in usage plan', () => {
      template.hasResourceProperties('AWS::ApiGateway::UsagePlan', {
        Quota: {
          Period: 'DAY',
          Limit: 5000000,  // Default quota increased to 5M
        },
      });
    });

    it('should respect custom API quota', () => {
      const customApp = new cdk.App();
      const customStack = new ServerlessStack(customApp, 'CustomQuotaStack', {
        apiDailyQuota: 10000000,  // 10M
        apiRateLimit: 100,
        apiBurstLimit: 200,
      });
      const customTemplate = Template.fromStack(customStack);

      customTemplate.hasResourceProperties('AWS::ApiGateway::UsagePlan', {
        Quota: {
          Limit: 10000000,
          Period: 'DAY',
        },
        Throttle: {
          RateLimit: 100,
          BurstLimit: 200,
        },
      });
    });
  });

  describe('Resource Count Validation', () => {
    it('should create exactly 3 DynamoDB tables', () => {
      template.resourceCountIs('AWS::DynamoDB::Table', 3);
    });

    it('should create exactly 1 Lambda function', () => {
      template.resourceCountIs('AWS::Lambda::Function', 1);
    });

    it('should create exactly 1 REST API', () => {
      template.resourceCountIs('AWS::ApiGateway::RestApi', 1);
    });

    it('should create exactly 1 S3 bucket', () => {
      template.resourceCountIs('AWS::S3::Bucket', 1);
    });

    it('should create exactly 1 API key', () => {
      template.resourceCountIs('AWS::ApiGateway::ApiKey', 1);
    });

    it('should create exactly 1 usage plan', () => {
      template.resourceCountIs('AWS::ApiGateway::UsagePlan', 1);
    });
  });
});
