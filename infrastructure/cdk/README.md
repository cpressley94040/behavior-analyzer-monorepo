# Behavior Analyzer CDK Infrastructure (Serverless)

AWS CDK infrastructure for deploying the Behavior Analyzer in serverless mode using AWS Lambda.

> **Note**: This branch contains the **serverless-only** deployment. For the full distributed deployment with ECS Fargate, Kinesis, and ElastiCache, see the `distributed` branch.

## Overview

This CDK application deploys a cost-optimized serverless infrastructure for behavioral anomaly detection:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              AWS Cloud                                       │
│                                                                              │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐                   │
│  │ API Gateway  │───▶│   Lambda     │───▶│  DynamoDB    │                   │
│  │  (REST API)  │    │  Processor   │    │   Tables     │                   │
│  └──────────────┘    └──────────────┘    └──────────────┘                   │
│                             │                                                │
│                             ▼                                                │
│                      ┌──────────────┐                                       │
│                      │      S3      │                                       │
│                      │   (Models)   │                                       │
│                      └──────────────┘                                       │
└─────────────────────────────────────────────────────────────────────────────┘
```

## Deployment Modes

| Mode | Branch | Best For | Est. Cost |
|------|--------|----------|-----------|
| **Serverless** | `main` | Single server, development, hobby | $50-100/month |
| **Distributed** | `distributed` | Multi-server, production, enterprise | $3,000-5,000/month |

## Stack

| Stack | Resources | Purpose |
|-------|-----------|---------|
| **ServerlessStack** | Lambda, API Gateway, DynamoDB, S3 | Complete serverless deployment |
| **BudgetsStack** | AWS Budgets, SNS Alerts | Cost monitoring |

## Prerequisites

### Required Tools

```bash
# Node.js 18+
node --version

# AWS CDK CLI
npm install -g aws-cdk
cdk --version  # >= 2.170

# AWS CLI v2 (configured with credentials)
aws --version
aws sts get-caller-identity  # Verify credentials
```

## Quick Start

### 1. Install Dependencies

```bash
cd infrastructure/cdk
npm install
```

### 2. Bootstrap CDK (First Time Only)

```bash
cdk bootstrap aws://ACCOUNT_ID/REGION
```

### 3. Deploy

```bash
# Deploy serverless stack
cdk deploy -a "npx ts-node bin/serverless-app.ts" --all

# With specific environment
ENVIRONMENT=production cdk deploy -a "npx ts-node bin/serverless-app.ts" --all
```

### 4. Get API Endpoint

```bash
aws cloudformation describe-stacks \
  --stack-name BehaviorAnalyzerServerless-dev \
  --query 'Stacks[0].Outputs[?OutputKey==`ApiUrl`].OutputValue' \
  --output text
```

## Configuration

### ServerlessStack Props

| Property | Default | Description |
|----------|---------|-------------|
| `environment` | `'dev'` | Environment name (dev, staging, prod) |
| `enableDetailedMetrics` | `false` | Enable CloudWatch detailed metrics |
| `lambdaMemorySize` | `512` | Lambda memory in MB |
| `lambdaTimeout` | `30` | Lambda timeout in seconds |
| `eventTtlDays` | `90` | DynamoDB TTL for events |

### API Gateway Limits

| Setting | Value |
|---------|-------|
| Rate Limit | 50 req/sec |
| Burst Limit | 100 concurrent |
| Daily Quota | 1,000,000 requests |

## Cost Estimates

### Serverless Mode

| Resource | Configuration | Est. Monthly Cost |
|----------|---------------|-------------------|
| Lambda | 512MB, ~1M invocations | ~$20 |
| API Gateway | ~1M requests | ~$4 |
| DynamoDB | On-demand, ~10GB | ~$25 |
| S3 | ~1GB models | ~$1 |
| CloudWatch | Logs, metrics | ~$5 |
| **Total** | | **~$55/month** |

### When to Upgrade to Distributed

Consider the `distributed` branch when:
- Supporting 3+ game servers
- Processing >100 events/second sustained
- Requiring <100ms detection latency
- Needing horizontal scaling

## API Endpoints

| Endpoint | Method | Auth | Description |
|----------|--------|------|-------------|
| `/ingest` | POST | API Key | Event ingestion |
| `/health` | GET | None | Health check |

### Example Request

```bash
curl -X POST https://your-api.execute-api.region.amazonaws.com/dev/ingest \
  -H "x-api-key: your-api-key" \
  -H "Content-Type: application/json" \
  -d '{
    "playerId": "player123",
    "actionType": "WEAPON_FIRED",
    "timestamp": "2024-01-01T12:00:00Z",
    "metadata": {
      "weapon": "AK47",
      "hits": 3,
      "accuracy": 0.75
    }
  }'
```

## Cleanup

```bash
cdk destroy -a "npx ts-node bin/serverless-app.ts" --all
```

## Switching to Distributed Mode

To upgrade to the full distributed deployment:

```bash
git checkout distributed
cdk deploy --all
```

See `distributed` branch README for full documentation.

## Related Documentation

- [Frontend Integration Guide](../../frontend/docs/INTEGRATION.md)
- [Rust Plugin Configuration](../../games/rust_plugin/README.md)
