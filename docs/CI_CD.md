# CI/CD Pipeline Documentation

This document describes the continuous integration and deployment pipeline for the Behavior Analyzer backend.

## Overview

The pipeline uses GitHub Actions to automatically build, test, and deploy changes to the C++ detection server.

```
┌──────────────┐    ┌──────────────┐    ┌──────────────┐    ┌──────────────┐
│    Build     │───▶│    Docker    │───▶│     CDK      │───▶│     ECS      │
│    & Test    │    │    Image     │    │   Deploy     │    │   Update     │
└──────────────┘    └──────────────┘    └──────────────┘    └──────────────┘
      │                   │                    │                    │
      │ Bazel build      │ Push to ECR       │ Deploy stacks      │ Force deploy
      │ Unit tests       │                    │                    │ Wait stable
      │ Format check     │                    │                    │ Health check
```

## Workflow File

Located at: `.github/workflows/deploy-backend.yml`

## Triggers

### Automatic Triggers

| Trigger | Branch | Paths | Action |
|---------|--------|-------|--------|
| Push | `main` | `backend/**`, `infrastructure/**`, `kore/**` | Full deploy |
| Pull Request | `main` | `backend/**`, `infrastructure/**`, `kore/**` | Build & test only |

### Manual Trigger

The workflow can be triggered manually via GitHub UI or CLI:

```bash
# Via GitHub CLI
gh workflow run deploy-backend.yml \
  --ref main \
  -f environment=production

# Or use the GitHub Actions UI:
# Actions > Deploy Backend > Run workflow
```

**Manual trigger options:**
- **environment**: `staging` (default) or `production`

## Pipeline Stages

### 1. Build and Test

**Runs on:** Every push and PR

**Steps:**
1. Checkout code
2. Set up Bazelisk
3. Restore Bazel cache
4. Build all targets (`bazel build //...`)
5. Run unit tests (`bazel test //test:all`)
6. Check code formatting
7. Build release binary (`bazel build -c opt //src/server:behavior_analyzer_server`)
8. Upload binary as artifact

**Duration:** ~5-10 minutes

### 2. Build Docker Image

**Runs on:** Push to main only (not PRs)
**Depends on:** Build and Test

**Steps:**
1. Download build artifacts
2. Configure AWS credentials (OIDC)
3. Login to ECR
4. Build Docker image with Buildx
5. Push to ECR with tags

**Image Tags:**
- `main-<sha>` - Commit SHA
- `main` - Branch name
- `latest` - Only on main branch

**Duration:** ~3-5 minutes

### 3. Deploy Infrastructure

**Runs on:** Push to main only
**Depends on:** Docker Image
**Environment:** staging or production

**Steps:**
1. Install Node.js and CDK dependencies
2. Configure AWS credentials
3. Run `cdk diff` to preview changes
4. Run `cdk deploy --all` to apply changes

**Duration:** ~5-15 minutes (depends on changes)

### 4. Deploy ECS Service

**Runs on:** Push to main only
**Depends on:** Docker Image, Infrastructure
**Environment:** staging or production

**Steps:**
1. Get current task definition
2. Update container image reference
3. Register new task definition
4. Update ECS service with new task definition
5. Wait for service to stabilize
6. Verify health endpoint

**Duration:** ~5-10 minutes

### 5. Notify

**Runs on:** Always (even on failure)

**Output:** GitHub Actions summary with deployment status

## Required Secrets

Configure these in GitHub repository settings (Settings > Secrets and variables > Actions):

| Secret | Description | Example |
|--------|-------------|---------|
| `AWS_DEPLOY_ROLE_ARN` | IAM role ARN for OIDC authentication | `arn:aws:iam::123456789012:role/GitHubDeployRole` |

### Setting Up OIDC Authentication

1. Create an IAM Identity Provider for GitHub Actions:
   ```bash
   aws iam create-open-id-connect-provider \
     --url https://token.actions.githubusercontent.com \
     --client-id-list sts.amazonaws.com \
     --thumbprint-list 6938fd4d98bab03faadb97b34396831e3780aea1
   ```

2. Create an IAM role with trust policy:
   ```json
   {
     "Version": "2012-10-17",
     "Statement": [
       {
         "Effect": "Allow",
         "Principal": {
           "Federated": "arn:aws:iam::ACCOUNT:oidc-provider/token.actions.githubusercontent.com"
         },
         "Action": "sts:AssumeRoleWithWebIdentity",
         "Condition": {
           "StringEquals": {
             "token.actions.githubusercontent.com:aud": "sts.amazonaws.com"
           },
           "StringLike": {
             "token.actions.githubusercontent.com:sub": "repo:YOUR_ORG/YOUR_REPO:*"
           }
         }
       }
     ]
   }
   ```

3. Attach policies to the role:
   - `AmazonEC2ContainerRegistryFullAccess`
   - `AmazonECS_FullAccess`
   - CloudFormation permissions for CDK
   - DynamoDB, S3, Kinesis, ElastiCache permissions

## Environment Protection Rules

Configure environment protection in GitHub (Settings > Environments):

### Staging Environment

- No required reviewers
- No wait timer
- Branch: `main` only

### Production Environment

- Required reviewers: 1+
- Wait timer: 5 minutes (optional)
- Branch: `main` only

## Caching

### Bazel Cache

The pipeline caches Bazel build artifacts:
- **Key:** `${{ runner.os }}-bazel-${{ hashFiles('backend/MODULE.bazel', 'backend/.bazelrc') }}`
- **Paths:** `~/.cache/bazel`, `~/.cache/bazelisk`

Cache is restored on subsequent runs to speed up builds.

### Docker Cache

Docker Buildx uses GitHub Actions cache:
- **Type:** `gha` (GitHub Actions cache)
- **Mode:** `max` (cache all layers)

## Rollback Procedures

### Quick Rollback (ECS)

```bash
# List recent task definitions
aws ecs list-task-definitions \
  --family-prefix behavior-analyzer-server \
  --sort DESC \
  --max-items 5

# Rollback to previous version
aws ecs update-service \
  --cluster behavior-analyzer-cluster \
  --service behavior-analyzer-server \
  --task-definition behavior-analyzer-server:PREVIOUS_REVISION
```

### Full Rollback (CDK)

```bash
# Revert to previous commit
git revert HEAD

# Push to trigger pipeline
git push origin main

# Or manually deploy specific commit
gh workflow run deploy-backend.yml --ref PREVIOUS_SHA
```

### Emergency Rollback

```bash
# Stop deployment immediately
aws ecs update-service \
  --cluster behavior-analyzer-cluster \
  --service behavior-analyzer-server \
  --deployment-configuration "maximumPercent=100,minimumHealthyPercent=100"

# Scale down to 0
aws ecs update-service \
  --cluster behavior-analyzer-cluster \
  --service behavior-analyzer-server \
  --desired-count 0
```

## Monitoring Deployments

### GitHub Actions UI

1. Go to Actions tab in repository
2. Select "Deploy Backend" workflow
3. View run details and logs

### AWS Console

- **ECS Console:** View service deployments, task status
- **CloudWatch Logs:** View application logs
- **CloudFormation:** View stack events

### CLI Commands

```bash
# Check deployment status
aws ecs describe-services \
  --cluster behavior-analyzer-cluster \
  --services behavior-analyzer-server \
  --query 'services[0].deployments'

# View recent events
aws ecs describe-services \
  --cluster behavior-analyzer-cluster \
  --services behavior-analyzer-server \
  --query 'services[0].events[:5]'
```

## Troubleshooting

### Build Failures

**Bazel build error:**
```bash
# Check build locally
cd backend
bazel build //...

# Check specific error
bazel build //src/server:behavior_analyzer_server --verbose_failures
```

**Format check failed:**
```bash
# Fix formatting
cd backend
bazel run //tools:format
git add -A && git commit -m "Fix formatting"
```

### Docker Failures

**ECR login failed:**
- Verify `AWS_DEPLOY_ROLE_ARN` secret is set correctly
- Check IAM role has ECR permissions

**Build failed:**
- Check Dockerfile syntax
- Verify build context includes necessary files

### CDK Failures

**Stack update failed:**
```bash
# Check stack events
aws cloudformation describe-stack-events \
  --stack-name BehaviorAnalyzerComputeStack \
  --query 'StackEvents[:10]'

# Rollback if needed
aws cloudformation cancel-update-stack --stack-name BehaviorAnalyzerComputeStack
```

### ECS Failures

**Tasks failing to start:**
```bash
# Check stopped tasks
aws ecs describe-tasks \
  --cluster behavior-analyzer-cluster \
  --tasks $(aws ecs list-tasks --cluster behavior-analyzer-cluster --desired-status STOPPED --query 'taskArns[0]' --output text) \
  --query 'tasks[0].stoppedReason'
```

**Health check failing:**
```bash
# Get ALB DNS
ALB_DNS=$(aws cloudformation describe-stacks \
  --stack-name BehaviorAnalyzerComputeStack \
  --query 'Stacks[0].Outputs[?OutputKey==`LoadBalancerDns`].OutputValue' \
  --output text)

# Test health endpoint
curl -v http://$ALB_DNS/health/ready
```

## Best Practices

### Before Merging to Main

1. Ensure all tests pass locally: `bazel test //test:all`
2. Check formatting: `bazel run //tools:format_check`
3. Review changes in PR
4. Get required approvals

### Deployment Windows

- **Staging:** Any time
- **Production:** Business hours (with rollback capability)

### Monitoring After Deployment

1. Watch deployment in GitHub Actions
2. Check ECS service events
3. Monitor CloudWatch dashboards
4. Verify health endpoints
5. Check application logs for errors

## Related Documentation

- [Deployment Guide](../backend/docs/DEPLOYMENT.md) - Infrastructure details
- [Operations Guide](../backend/docs/OPERATIONS.md) - Monitoring and troubleshooting
- [CDK README](../infrastructure/cdk/README.md) - Infrastructure code
