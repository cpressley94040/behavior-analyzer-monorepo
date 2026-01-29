import * as cdk from 'aws-cdk-lib';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import * as ecs from 'aws-cdk-lib/aws-ecs';
import * as elbv2 from 'aws-cdk-lib/aws-elasticloadbalancingv2';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as logs from 'aws-cdk-lib/aws-logs';
import { Construct } from 'constructs';

export interface ComputeStackProps extends cdk.StackProps {
  /**
   * Environment name (e.g., 'dev', 'prod')
   */
  environment?: string;

  /**
   * ECR repository URI for the container image
   * Example: '123456789012.dkr.ecr.us-east-2.amazonaws.com/behavior-analyzer:latest'
   */
  containerImageUri: string;

  /**
   * ECS cluster name (default: 'behavior-analyzer-cluster')
   */
  clusterName?: string;

  /**
   * Fargate service name (default: 'behavior-analyzer-server')
   */
  serviceName?: string;

  /**
   * Desired number of Fargate tasks (default: 2)
   */
  desiredCount?: number;

  /**
   * CPU units for the task (default: 1024 = 1 vCPU)
   */
  taskCpu?: number;

  /**
   * Memory in MB for the task (default: 2048 = 2 GB)
   */
  taskMemoryMiB?: number;

  /**
   * Use default VPC instead of creating a new one (default: true)
   */
  useDefaultVpc?: boolean;

  /**
   * Health check interval in seconds (default: 30)
   */
  healthCheckIntervalSeconds?: number;

  /**
   * Health check timeout in seconds (default: 5)
   */
  healthCheckTimeoutSeconds?: number;

  /**
   * Healthy threshold count (default: 2)
   */
  healthyThresholdCount?: number;

  /**
   * Unhealthy threshold count (default: 3)
   */
  unhealthyThresholdCount?: number;
}

/**
 * Compute Stack - ECS Fargate deployment for the Behavior Analyzer backend.
 *
 * This stack provides a containerized ECS Fargate service with Application Load Balancer,
 * health checks, and CloudWatch logging for the C++ behavior analysis server.
 *
 * @remarks
 * **Architecture:**
 * ```
 * ALB → ECS Fargate Task (Container)
 *  ↓        ↓
 * /health  /metrics
 * (8080)   (9090)
 * ```
 *
 * **Key Features:**
 * - Application Load Balancer with health checks on /health/ready
 * - ECS Fargate service with auto-scaling capability
 * - CloudWatch log group for container logs
 * - Security groups for ALB and ECS tasks
 * - IAM roles for task execution and task permissions
 *
 * **Ports:**
 * - 8080: Health check endpoint (/health/ready, /health/live)
 * - 9090: Prometheus metrics endpoint (/metrics)
 *
 * @example
 * ```typescript
 * const computeStack = new ComputeStack(app, 'BehaviorAnalyzerComputeStack', {
 *   environment: 'prod',
 *   containerImageUri: '123456789012.dkr.ecr.us-east-2.amazonaws.com/behavior-analyzer:latest',
 *   desiredCount: 2,
 *   taskCpu: 1024,
 *   taskMemoryMiB: 2048,
 * });
 * ```
 */
export class ComputeStack extends cdk.Stack {
  public readonly cluster: ecs.Cluster;
  public readonly service: ecs.FargateService;
  public readonly loadBalancer: elbv2.ApplicationLoadBalancer;
  public readonly logGroup: logs.LogGroup;

  constructor(scope: Construct, id: string, props: ComputeStackProps) {
    super(scope, id, props);

    const environment = props.environment || 'dev';
    const clusterName = props.clusterName || 'behavior-analyzer-cluster';
    const serviceName = props.serviceName || 'behavior-analyzer-server';
    const desiredCount = props.desiredCount || 2;
    const taskCpu = props.taskCpu || 1024;
    const taskMemoryMiB = props.taskMemoryMiB || 2048;
    const useDefaultVpc = props.useDefaultVpc !== false; // Default to true
    const healthCheckInterval = props.healthCheckIntervalSeconds || 30;
    const healthCheckTimeout = props.healthCheckTimeoutSeconds || 5;
    const healthyThreshold = props.healthyThresholdCount || 2;
    const unhealthyThreshold = props.unhealthyThresholdCount || 3;

    // =========================================================================
    // VPC
    // =========================================================================

    const vpc = useDefaultVpc
      ? ec2.Vpc.fromLookup(this, 'DefaultVpc', { isDefault: true })
      : new ec2.Vpc(this, 'Vpc', {
          maxAzs: 2,
          natGateways: 1,
        });

    // =========================================================================
    // ECS Cluster
    // =========================================================================

    this.cluster = new ecs.Cluster(this, 'Cluster', {
      clusterName: `${clusterName}-${environment}`,
      vpc,
      enableFargateCapacityProviders: true,
    });

    // =========================================================================
    // CloudWatch Log Group
    // =========================================================================

    this.logGroup = new logs.LogGroup(this, 'LogGroup', {
      logGroupName: `/ecs/${serviceName}`,
      retention: logs.RetentionDays.TWO_WEEKS,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    // =========================================================================
    // IAM Roles
    // =========================================================================

    // Task Execution Role - used by ECS to pull images and write logs
    const executionRole = new iam.Role(this, 'ExecutionRole', {
      assumedBy: new iam.ServicePrincipal('ecs-tasks.amazonaws.com'),
      managedPolicies: [
        iam.ManagedPolicy.fromAwsManagedPolicyName('service-role/AmazonECSTaskExecutionRolePolicy'),
      ],
    });

    // Add ECR permissions to execution role (for pulling images)
    executionRole.addToPolicy(
      new iam.PolicyStatement({
        effect: iam.Effect.ALLOW,
        actions: [
          'ecr:GetAuthorizationToken',
          'ecr:BatchCheckLayerAvailability',
          'ecr:GetDownloadUrlForLayer',
          'ecr:BatchGetImage',
        ],
        resources: ['*'],
      })
    );

    // Task Role - used by the container itself (for AWS SDK calls)
    const taskRole = new iam.Role(this, 'TaskRole', {
      assumedBy: new iam.ServicePrincipal('ecs-tasks.amazonaws.com'),
      description: 'Role for Behavior Analyzer server task',
    });

    // =========================================================================
    // Task Definition
    // =========================================================================

    const taskDefinition = new ecs.FargateTaskDefinition(this, 'TaskDefinition', {
      family: `${serviceName}-${environment}`,
      cpu: taskCpu,
      memoryLimitMiB: taskMemoryMiB,
      executionRole,
      taskRole,
    });

    const container = taskDefinition.addContainer('ServerContainer', {
      image: ecs.ContainerImage.fromRegistry(props.containerImageUri),
      logging: ecs.LogDriver.awsLogs({
        logGroup: this.logGroup,
        streamPrefix: 'ecs',
      }),
      environment: {
        ENVIRONMENT: environment,
        LOG_LEVEL: environment === 'prod' ? 'info' : 'debug',
      },
      healthCheck: {
        command: ['CMD-SHELL', 'curl -f http://localhost:8080/health/ready || exit 1'],
        interval: cdk.Duration.seconds(30),
        timeout: cdk.Duration.seconds(5),
        retries: 3,
        startPeriod: cdk.Duration.seconds(60),
      },
    });

    // Port 8080: Health check endpoints
    container.addPortMappings({
      containerPort: 8080,
      protocol: ecs.Protocol.TCP,
      name: 'health',
    });

    // Port 9090: Prometheus metrics endpoint
    container.addPortMappings({
      containerPort: 9090,
      protocol: ecs.Protocol.TCP,
      name: 'metrics',
    });

    // =========================================================================
    // Security Groups
    // =========================================================================

    const albSecurityGroup = new ec2.SecurityGroup(this, 'AlbSecurityGroup', {
      vpc,
      description: 'Security group for Behavior Analyzer ALB',
      allowAllOutbound: true,
    });

    // Allow HTTP traffic from anywhere
    albSecurityGroup.addIngressRule(
      ec2.Peer.anyIpv4(),
      ec2.Port.tcp(80),
      'Allow HTTP traffic'
    );

    const serviceSecurityGroup = new ec2.SecurityGroup(this, 'ServiceSecurityGroup', {
      vpc,
      description: 'Security group for Behavior Analyzer ECS service',
      allowAllOutbound: true,
    });

    // Allow traffic from ALB to health port
    serviceSecurityGroup.addIngressRule(
      albSecurityGroup,
      ec2.Port.tcp(8080),
      'Allow traffic from ALB to health port'
    );

    // Allow traffic from ALB to metrics port
    serviceSecurityGroup.addIngressRule(
      albSecurityGroup,
      ec2.Port.tcp(9090),
      'Allow traffic from ALB to metrics port'
    );

    // =========================================================================
    // Application Load Balancer
    // =========================================================================

    this.loadBalancer = new elbv2.ApplicationLoadBalancer(this, 'LoadBalancer', {
      loadBalancerName: `${serviceName}-${environment}`,
      vpc,
      internetFacing: true,
      securityGroup: albSecurityGroup,
    });

    const targetGroup = new elbv2.ApplicationTargetGroup(this, 'TargetGroup', {
      targetGroupName: `${serviceName}-${environment}`,
      vpc,
      port: 8080,
      protocol: elbv2.ApplicationProtocol.HTTP,
      targetType: elbv2.TargetType.IP,
      healthCheck: {
        path: '/health/ready',
        port: '8080',
        protocol: elbv2.Protocol.HTTP,
        interval: cdk.Duration.seconds(healthCheckInterval),
        timeout: cdk.Duration.seconds(healthCheckTimeout),
        healthyThresholdCount: healthyThreshold,
        unhealthyThresholdCount: unhealthyThreshold,
        healthyHttpCodes: '200',
      },
      deregistrationDelay: cdk.Duration.seconds(30),
    });

    this.loadBalancer.addListener('HttpListener', {
      port: 80,
      protocol: elbv2.ApplicationProtocol.HTTP,
      defaultAction: elbv2.ListenerAction.forward([targetGroup]),
    });

    // =========================================================================
    // ECS Fargate Service
    // =========================================================================

    this.service = new ecs.FargateService(this, 'Service', {
      serviceName: `${serviceName}-${environment}`,
      cluster: this.cluster,
      taskDefinition,
      desiredCount,
      assignPublicIp: true, // Required for default VPC without NAT Gateway
      securityGroups: [serviceSecurityGroup],
      healthCheckGracePeriod: cdk.Duration.seconds(120),
      enableExecuteCommand: true, // Allows ECS Exec for debugging
      minHealthyPercent: 100,
      maxHealthyPercent: 200,
      circuitBreaker: { enable: true, rollback: true },
    });

    // Prevent CloudFormation from waiting too long for service stability
    // (avoids OIDC token expiry during first deployment)
    const cfnService = this.service.node.defaultChild as ecs.CfnService;
    cfnService.addPropertyOverride('DeploymentConfiguration.DeploymentCircuitBreaker', {
      Enable: true,
      Rollback: true,
    });

    this.service.attachToApplicationTargetGroup(targetGroup);

    // =========================================================================
    // Outputs
    // =========================================================================

    new cdk.CfnOutput(this, 'LoadBalancerDns', {
      value: this.loadBalancer.loadBalancerDnsName,
      description: 'DNS name of the Application Load Balancer',
      exportName: `BehaviorAnalyzerLoadBalancerDns-${environment}`,
    });

    new cdk.CfnOutput(this, 'ClusterName', {
      value: this.cluster.clusterName,
      description: 'ECS Cluster name',
      exportName: `BehaviorAnalyzerClusterName-${environment}`,
    });

    new cdk.CfnOutput(this, 'ServiceName', {
      value: this.service.serviceName,
      description: 'ECS Service name',
      exportName: `BehaviorAnalyzerServiceName-${environment}`,
    });

    new cdk.CfnOutput(this, 'LogGroupName', {
      value: this.logGroup.logGroupName,
      description: 'CloudWatch Log Group name',
      exportName: `BehaviorAnalyzerLogGroup-${environment}`,
    });

    new cdk.CfnOutput(this, 'HealthEndpoint', {
      value: `http://${this.loadBalancer.loadBalancerDnsName}/health/ready`,
      description: 'Health check endpoint URL',
    });

    new cdk.CfnOutput(this, 'MetricsEndpoint', {
      value: `http://${this.loadBalancer.loadBalancerDnsName}:9090/metrics`,
      description: 'Prometheus metrics endpoint URL (requires port 9090 listener)',
    });
  }
}
