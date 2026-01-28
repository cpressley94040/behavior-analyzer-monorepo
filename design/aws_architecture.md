# AWS Architecture & Implementation Guide

## 1. Executive Summary

This document details the technical implementation of the **Behavior Analysis Web App** using AWS cloud services. It translates the high-level requirements defined in `web_app.md` into concrete AWS infrastructure, leveraging **AWS Amplify Gen 2** for rapid application development and **Serverless** architecture for scalable event ingestion and processing.

> **Note:** This document describes the **frontend/web app architecture** (Amplify + Lambda). For the **C++ backend anomaly detection** deployment, see:
> - `infrastructure/cdk/` - CDK stacks for ECS Fargate, Kinesis, DynamoDB, Redis
> - `backend/docs/DEPLOYMENT.md` - Backend deployment guide
>
> The two subsystems complement each other:
> - **Frontend (this doc)**: Web dashboard, user authentication, data visualization
> - **Backend (CDK)**: High-performance C++ detection engine, streaming processing

## 2. Architecture Overview

 The system is divided into two primary subsystems:
1.  **Ingestion Subsystem**: High-throughput, serverless pipeline for handling telemetry from game servers.
2.  **Management Subsystem**: Web application for investigators to review flags, visualize data, and manage configurations.

### 2.1 High-Level Architecture Diagram

```mermaid
graph TD
    subgraph "Game Server (Rust)"
        Plugin[Rust Plugin] -->|HTTPS POST /ingest| APIG[API Gateway]
    end

    subgraph "Ingestion Pipeline"
        APIG -->|Direct Put| Kinesis[Kinesis Data Firehose]
        Kinesis -->|Batch| S3Raw[S3 Bucket\n(Raw Events)]
        S3Raw -->|Event Notification| IngestLambda[Ingest Lambda]
    end

    subgraph "Data Processing & Storage"
        IngestLambda -->|Update| DDB[DynamoDB\n(Single Table Design)]
        IngestLambda -->|Calculate| CW[CloudWatch Metrics]
        DDB -->|Stream| StreamLambda[Detection Lambda]
        StreamLambda -->|Flag| DDB
    end

    subgraph "Management Web App (Amplify)"
        Browser[Investigator Browser] -->|HTTPS| CloudFront[Amplify Hosting]
        Browser -->|GraphQL| AppSync[AppSync API]
        AppSync -->|Resolver| DDB
        Browser -->|Auth| Cognito[Cognito User Pool]
        Cognito -->|Identity| AppSync
    end
```

## 3. Component Details

### 3.1 Authentication & Authorization (Amazon Cognito)

We utilize **Amazon Cognito** to handle two distinct types of identities:

1.  **Investigator Access (User Pools)**:
    *   **Role**: Human users (Admins, Moderators) accessing the web dashboard.
    *   **Method**: Username/Password with MFA, or SSO.
    *   **Groups**: `Admin`, `Investigator`.
    *   **Implementation**: Defined in `amplify/auth/resource.ts`.

2.  **Game Server Access (M2M)**:
    *   **Role**: Rust servers sending telemetry.
    *   **Method**: OAuth 2.0 Client Credentials flow (recommended) or API Keys.
    *   **Implementation**: Cognito User Pool "App Clients" with `client_credentials` flow.
    *   **Scope**: `ingest/write`.

### 3.2 Frontend Hosting (AWS Amplify)

*   **Framework**: React + Vite.
*   **Hosting**: AWS Amplify Hosting (serverless static site hosting with CI/CD).
*   **Build**: Automated pipeline connected to the GitHub repository.
*   **Environment**: Per-branch deployments (dev, staging, prod).

### 3.3 Telemetry Ingestion (API Gateway + Kinesis)

Direct integration between specific AWS services avoids Lambda "cold starts" and reduces costs for the high-volume ingestion path.

*   **API Gateway (REST API)**:
    *   **Endpoint**: `/ingest`.
    *   **Auth**: Cognito Authorizer (validates the Game Server's OAuth token).
    *   **Integration**: AWS Service Integration directly to Kinesis Firehose.
*   **Kinesis Data Firehose**:
    *   **Role**: Buffer and batch incoming JSON events.
    *   **Buffer Size**: 5MB or 60 seconds.
    *   **Destination**: Amazon S3 (Raw Data Lake).

### 3.4 Data Lake (Amazon S3)

*   **Bucket Structure**: `s3://<bucket-name>/raw/year=YYYY/month=MM/day=DD/hour=HH/`
*   **Format**: GZIP compressed JSON.
*   **Lifecycle**: Transition to Glacier after 30 days for cost optimization.
*   **Purpose**: Immutable source of truth. Allows re-running ML models on historical data.

### 3.5 Operational Database (Amazon DynamoDB)

We adhere to the Single Table Design outlined in `web_app.md`.

*   **Tables**:
    1.  **MainTable (Application Data)**: Stores User Profiles, Configs, limited recent events if needed for UI.
    2.  **AnalyticsTable (Calculated Stats)**: Optimized for the read patterns of the dashboard.
*   **Access**:
    *   **AppSync**: Connects directly via VTL or JavaScript resolvers for dashboard queries.
    *   **Lambdas**: Perform write-heavy aggregation logic.

### 3.6 API Layer (AWS AppSync)

*   **Type**: Managed GraphQL.
*   **Schema**: Generic definitions for `Player`, `Flag`, `Event` (see `amplify/data/resource.ts`).
*   **Real-time**: Subscriptions enabled for `onCreateFlag` to alert investigators instantly.

## 4. Implementation Strategy (Amplify Gen 2)

We will use the **AWS Amplify Gen 2** code-first approach (`amplify/backend.ts`) to define the infrastructure.

### 4.1 Directory Structure

```text
amplify/
├── auth/
│   └── resource.ts       # Cognito definition
├── data/
│   └── resource.ts       # AppSync Schema & DynamoDB
├── functions/
│   ├── ingest/           # Telemetry processing
│   └── triggers/         # Auth triggers
├── backend.ts            # Entry point
└── package.json
```

### 4.2 Custom Resources (CDK)

While Amplify handles Auth and Data well, the high-performance Ingestion Pipeline (Kinesis) requires low-level definition. Amplify Gen 2 supports mixing CDK constructs.

**Proposed CDK Additions using `backend.addOutput` or `defineBackend`:**

1.  **Kinesis Stream**: Define the Firehose delivery stream.
2.  **Ingestion Policy**: Grant the API Gateway role permission to `firehose:PutRecord`.

## 5. Security Architecture

1.  **Encryption**:
    *   At rest: S3 (SSE-S3), DynamoDB (AWS Owned Key), Kinesis (KMS).
    *   In transit: TLS 1.2+ for all API calls.
2.  **Network**:
    *   CloudFront implementation for the Web App (DDoS protection via AWS Shield Standard).
    *   WAF (Web Application Firewall) attached to API Gateway to rate limit and block malicious IPs.
3.  **Governance**:
    *   Least-privilege IAM roles for distinct Lambda functions.
    *   Audit logs via CloudTrail.

## 6. Scalability & Limits

| Component | Limit Strategy |
|-----------|----------------|
| **API Gateway** | Default 10k RPS. Soft limit, can be raised. Throttling applied per-tenant. |
| **Kinesis** | Scale shards based on throughput. Firehose auto-scales. |
| **DynamoDB** | On-demand capacity mode handles spiky game traffic. |
| **Lambda** | Concurrency limits managed to protect downstream resources. |

## 7. C++ Backend Integration

The C++ anomaly detection backend (`infrastructure/cdk/`) provides high-performance streaming detection:

### Architecture Comparison

| Aspect | Frontend (This Doc) | C++ Backend |
|--------|--------------------|----|
| **Compute** | Lambda (serverless) | ECS Fargate (containers) |
| **Streaming** | Kinesis Firehose → S3 | Kinesis Data Streams → ECS |
| **Database** | DynamoDB (single table) | DynamoDB (3 tables), Redis |
| **Detection** | Lambda-based | C++ `ManagedDetectionPipeline` |
| **Use Case** | Dashboard, investigation | Real-time streaming detection |

### Integration Points

1. **Event Flow**: Game servers → API Gateway → Kinesis → C++ Backend → DynamoDB → Frontend Dashboard
2. **Shared Data**: Both systems read/write to DynamoDB (anomaly flags, entity state)
3. **Separate Deployment**: CDK stacks are independent; deploy frontend (Amplify) and backend (CDK) separately

### Backend CDK Stacks

```bash
cd infrastructure/cdk
cdk deploy --all
```

- **NetworkStack**: VPC, subnets, VPC endpoints
- **DataStack**: DynamoDB tables, S3 buckets, ElastiCache Redis
- **StreamingStack**: Kinesis Data Streams
- **ComputeStack**: ECS Fargate service with auto-scaling
