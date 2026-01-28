# Serverless Deployment (Lambda-Only)

Cost-optimized architecture for **1 Rust server with up to 500 players**.

**Estimated Monthly Cost: $50-100**

## Key Optimization: Interesting Events Only

This architecture **only stores events that matter** for cheat detection:

| Event Type | Stored? | Reason |
|------------|---------|--------|
| `SESSION_START`, `SESSION_END` | ✅ Always | Session tracking |
| `PLAYER_KILLED` | ✅ Always | Kill analysis |
| `PLAYER_REPORTED`, `PLAYER_VIOLATION` | ✅ Always | Admin actions |
| High accuracy shots (>70%) | ✅ Yes | Suspicious |
| High headshot ratio (>50%) | ✅ Yes | Suspicious |
| Events triggering detections | ✅ Yes | Evidence |
| `WEAPON_FIRED` (normal) | ❌ Stats only | Updates player stats, not stored |
| `PLAYER_INPUT`, `PLAYER_TICK` | ❌ Stats only | High volume, low value |
| Looting, gathering, building | ❌ Stats only | Not relevant to combat cheats |

**Result: 80-95% reduction in DynamoDB writes** while maintaining full detection capability.

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    SERVERLESS ARCHITECTURE (OPTIMIZED)                     │
│                                                                          │
│   Rust Server                                                            │
│       │                                                                  │
│       ▼                                                                  │
│   ┌─────────────────┐                                                   │
│   │  API Gateway    │ ◄── Rate limiting, API key auth                   │
│   │  (REST API)     │     $35/month @ 10M requests                      │
│   └────────┬────────┘                                                   │
│            │                                                             │
│            ▼                                                             │
│   ┌─────────────────┐     ┌─────────────────────────────────────┐       │
│   │     Lambda      │────►│ Process ALL events for stats        │       │
│   │  (Python 3.11)  │     │ Store ONLY interesting events       │       │
│   │     $15/mo      │     │ Run detection on every batch        │       │
│   └────────┬────────┘     └─────────────────────────────────────┘       │
│            │                                                             │
│      ┌─────┴─────┐                                                      │
│      ▼           ▼                                                       │
│  ┌────────┐  ┌────────┐                                                 │
│  │DynamoDB│  │   S3   │                                                 │
│  │ Tables │  │ Models │                                                 │
│  │ $10/mo │  │ $0.50  │  ◄── 80-95% fewer writes!                       │
│  └────────┘  └────────┘                                                 │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

## Cost Breakdown

| Service | Configuration | Monthly Cost |
|---------|---------------|--------------|
| API Gateway | REST API, 10M requests | $35.00 |
| Lambda | 10M invocations, 512MB, 300ms avg | $15.00 |
| DynamoDB | On-demand, ~10GB storage (filtered) | $10.00 |
| S3 | 1GB model storage | $0.50 |
| CloudWatch | Logs + basic metrics | $5.00 |
| **Total** | | **~$65/month** |

## Prerequisites

1. **AWS Account** with CDK bootstrap completed
2. **Node.js** 18+ and npm
3. **AWS CLI** configured with credentials
4. **Python 3.11** (for Lambda)

## Deployment Steps

### 1. Install Dependencies

```bash
cd infrastructure/cdk
npm install
```

### 2. Configure Environment

```bash
# Set AWS region (must match Rust plugin config)
export CDK_DEFAULT_REGION=us-east-2
export CDK_DEFAULT_ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
```

### 3. Deploy the Stack

```bash
# Deploy to dev environment
npx cdk deploy -a "npx ts-node bin/serverless-app.ts" --all

# Deploy to prod environment
npx cdk deploy -a "npx ts-node bin/serverless-app.ts" --all --context environment=prod
```

### 4. Get API Key

After deployment, retrieve the API key:

```bash
# Get the API Key ID from stack outputs
API_KEY_ID=$(aws cloudformation describe-stacks \
  --stack-name BehaviorAnalyzerServerless-dev \
  --query "Stacks[0].Outputs[?OutputKey=='ApiKeyId'].OutputValue" \
  --output text)

# Get the actual API key value
aws apigateway get-api-key --api-key $API_KEY_ID --include-value \
  --query "value" --output text
```

### 5. Configure Rust Plugin

Update your Rust server's plugin configuration:

```json
{
  "ApiEndpoint": "https://xxxxxxxxxx.execute-api.us-east-2.amazonaws.com/dev/ingest",
  "ApiKey": "your-api-key-here",
  "BatchSize": 50,
  "FlushIntervalMs": 2000,
  "EnabledEvents": [
    "PLAYER_ATTACK", "MELEE_ATTACK", "PLAYER_KILLED", "WEAPON_FIRED",
    "SESSION_START", "SESSION_END"
  ]
}
```

**Recommended: Disable high-frequency events** for cost savings:
- Remove `PLAYER_INPUT` and `PLAYER_TICK` from `EnabledEvents`
- Set `PlayerInputSampleIntervalMs` to 1000+ if needed

## Plugin Configuration for Serverless

```json
{
  "ApiEndpoint": "https://YOUR-API-ID.execute-api.us-east-2.amazonaws.com/dev/ingest",
  "ApiKey": "YOUR-API-KEY",
  "ServerKey": "your-server-identifier",

  "EnabledEvents": [
    "PLAYER_ATTACK",
    "MELEE_ATTACK",
    "PLAYER_KILLED",
    "WEAPON_FIRED",
    "ENTITY_DAMAGE",
    "SESSION_START",
    "SESSION_END",
    "ITEM_LOOTED"
  ],

  "BatchSize": 50,
  "FlushIntervalMs": 2000,
  "MaxRetries": 3,
  "RetryDelayMs": 5000,
  "UsePooling": true,
  "MaxQueueSize": 1000
}
```

## Testing the Deployment

### Health Check

```bash
API_ENDPOINT=$(aws cloudformation describe-stacks \
  --stack-name BehaviorAnalyzerServerless-dev \
  --query "Stacks[0].Outputs[?OutputKey=='ApiEndpoint'].OutputValue" \
  --output text)

curl "${API_ENDPOINT}health"
# Response: {"status": "healthy", "version": "1.0.0"}
```

### Send Test Events

```bash
API_KEY="your-api-key-here"

curl -X POST "${API_ENDPOINT}ingest" \
  -H "Content-Type: application/json" \
  -H "x-api-key: ${API_KEY}" \
  -d '{
    "events": [
      {
        "owner": "test-server",
        "playerId": "player-123",
        "actionType": "WEAPON_FIRED",
        "metadata": {"accuracy": 0.85, "hits": 17, "shots": 20}
      }
    ]
  }'
```

## Tuning Interesting Event Thresholds

The Lambda uses thresholds to determine which events are "interesting" enough to store:

| Environment Variable | Default | Description |
|---------------------|---------|-------------|
| `ACCURACY_INTERESTING_THRESHOLD` | 0.7 | Store shots with ≥70% accuracy |
| `HEADSHOT_INTERESTING_THRESHOLD` | 0.5 | Store events with ≥50% headshot ratio |
| `ZSCORE_THRESHOLD` | 3.0 | Flag players with z-score > 3.0 |
| `MIN_SHOTS_FOR_INTERESTING` | 5 | Minimum shots to evaluate accuracy |

### Adjusting Thresholds

**More storage, more evidence** (lower thresholds):
```bash
# Store events with 50% accuracy or higher
npx cdk deploy -a "npx ts-node bin/serverless-app.ts" \
  --context accuracyInterestingThreshold=0.5
```

**Less storage, only obvious cheaters** (higher thresholds):
```bash
# Only store events with 90% accuracy or higher
npx cdk deploy -a "npx ts-node bin/serverless-app.ts" \
  --context accuracyInterestingThreshold=0.9
```

### Viewing Filter Stats

Lambda logs show what was stored vs skipped:

```
Processed 50 events: stored 3 interesting, skipped 47 routine, 1 detections
```

## Monitoring

### CloudWatch Dashboards

View Lambda metrics in CloudWatch:
- Invocations
- Duration
- Errors
- Throttles

### Useful Queries

```bash
# View recent Lambda logs
aws logs tail /aws/lambda/behavior-analyzer-processor-dev --follow

# Check DynamoDB item count
aws dynamodb scan --table-name behavior-analyzer-events-dev \
  --select COUNT --query "Count"
```

## Scaling to Option B (Lambda + Kinesis)

When you need to scale beyond 1 server:

1. Add Kinesis stream between API Gateway and Lambda
2. Change Lambda trigger from API Gateway to Kinesis
3. Update API Gateway to write directly to Kinesis

**When to scale:**
- Processing latency > 2 seconds
- Lambda throttling occurring
- 5+ Rust servers
- 2500+ concurrent players

## Destroying the Stack

```bash
# WARNING: This deletes all data
npx cdk destroy -a "npx ts-node bin/serverless-app.ts" --all

# Note: DynamoDB tables with RETAIN policy must be deleted manually
aws dynamodb delete-table --table-name behavior-analyzer-events-dev
aws dynamodb delete-table --table-name behavior-analyzer-players-dev
aws dynamodb delete-table --table-name behavior-analyzer-detections-dev
```

## Troubleshooting

### "Missing Authentication Token" Error

- Ensure you're including the `x-api-key` header
- Verify the API key is correct

### Lambda Timeout

- Increase `lambdaTimeout` in CDK stack
- Reduce `BatchSize` in plugin config

### High Costs

- Disable `PLAYER_INPUT` and `PLAYER_TICK` events
- Increase `FlushIntervalMs` to reduce API calls
- Enable DynamoDB auto-scaling (switch to PROVISIONED mode)

## Security Considerations

1. **API Key Rotation**: Rotate API keys periodically
2. **WAF**: Consider adding AWS WAF for DDoS protection
3. **VPC**: For production, consider VPC with private subnets
4. **Encryption**: All data encrypted at rest (S3, DynamoDB)
