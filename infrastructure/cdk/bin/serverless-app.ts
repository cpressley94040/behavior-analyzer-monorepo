#!/usr/bin/env node
import 'source-map-support/register';
import * as cdk from 'aws-cdk-lib';
import { ServerlessStack } from '../lib/serverless-stack';

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

app.synth();
