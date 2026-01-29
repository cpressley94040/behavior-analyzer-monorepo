#!/usr/bin/env node
import 'source-map-support/register';
import * as cdk from 'aws-cdk-lib';
import { ServerlessStack } from '../lib/serverless-stack';
import { ComputeStack } from '../lib/compute-stack';

/**
 * Serverless Deployment - Lambda-Only Infrastructure
 *
 * Cost-optimized serverless architecture for single Rust server deployment.
 * Estimated cost: $50-100/month for 1 server, 500 players.
 *
 * Usage:
 *   cd infrastructure/cdk
 *   npm install
 *   npx cdk deploy -a "npx ts-node bin/serverless-app.ts" --all
 *
 * To destroy:
 *   npx cdk destroy -a "npx ts-node bin/serverless-app.ts" --all
 */
const app = new cdk.App();

const environment = app.node.tryGetContext('environment') || 'dev';
const region = process.env.CDK_DEFAULT_REGION || 'us-east-2';
const account = process.env.CDK_DEFAULT_ACCOUNT;

// Serverless Stack (Lambda + API Gateway)
new ServerlessStack(app, `BehaviorAnalyzerServerless-${environment}`, {
  env: {
    account,
    region,
  },
  environment,
  description: `Behavior Analyzer Serverless Stack - ${environment}`,

  // Configuration options
  lambdaMemorySize: 512,     // Increase if processing is slow
  lambdaTimeout: 30,         // Seconds
  eventTtlDays: 90,          // How long to keep events
  enableDetailedMetrics: environment === 'prod',
});

// Compute Stack (ECS Fargate)
// Only deploy if containerImageUri is provided via context
const containerImageUri = app.node.tryGetContext('containerImageUri');
if (containerImageUri) {
  new ComputeStack(app, 'BehaviorAnalyzerComputeStack', {
    env: {
      account: account || process.env.CDK_DEFAULT_ACCOUNT,
      region: region,
    },
    environment,
    description: `Behavior Analyzer Compute Stack (ECS Fargate) - ${environment}`,
    containerImageUri,

    // Configuration options
    // desiredCount 0 allows CDK to create the stack without waiting for task stability.
    // The verify-deployment job scales up and checks health.
    desiredCount: 0,
    taskCpu: 1024,        // 1 vCPU
    taskMemoryMiB: 2048,  // 2 GB
    useDefaultVpc: true,  // Use default VPC to reduce costs
  });
}

app.synth();
