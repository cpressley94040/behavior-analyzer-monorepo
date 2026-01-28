# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Behavior Analyzer is a general-purpose behavioral outlier detection platform for streaming human behavior data. The project has four main components:

- **backend/**: C++20 high-performance anomaly detection library (Bazel)
- **frontend/**: React + Vite web dashboard with AWS Amplify
- **games/rust_plugin/**: C# Oxide/uMod plugin for Rust game servers
- **kore/**: C++20 header-only data structures framework

## Build & Test Commands

### Backend (C++)

```bash
cd backend

# Build
bazel build //...                    # All targets
bazel build //:behavior_analyzer     # Main library
bazel build //src/server:behavior_analyzer_server  # Production server binary

# Test
bazel test //test:all                # All unit tests
bazel test //test:core_test          # Specific test suite
bazel test //test:all --test_output=all  # Verbose output

# Fuzz tests
bazel run //fuzz:streaming_cusum_fuzz
bazel run //fuzz:mpsc_queue_fuzz -- -max_total_time=60

# Benchmarks (use -c opt for accurate measurements)
bazel run -c opt //benchmark:all_bench
bazel run -c opt //benchmark:detectors_bench -- --benchmark_filter="BM_StreamingCUSUM.*"

# Code formatting
bazel run //tools:format             # Format all C++ files
bazel run //tools:format_check       # Check formatting (CI)

# Coverage
./tools/coverage.sh                  # All tests with coverage report

# Run server locally
./bazel-bin/src/server/behavior_analyzer_server --config config/development.yaml
```

### Frontend (React)

```bash
cd frontend
npm install
npm run dev      # Dev server at http://localhost:5173
npm run build    # Production build
npm run lint     # ESLint
```

### Kore Framework

```bash
cd kore
bazel test //src/tests/unit:all      # Unit tests
bazel test //src/tests/fuzz:all      # Fuzz tests
./scripts/coverage.sh                # Coverage report
```

**Coverage Expectations (Kore):**
| Metric | Baseline | Target |
|--------|----------|--------|
| Line coverage | 91% | ≥90% |
| Function coverage | 97% | ≥95% |
| Region coverage | 93% | ≥90% |
| Branch coverage | 55% | ≥50% |

### Rust Game Plugin (C#)

```bash
cd games/rust_plugin/test
/usr/local/share/dotnet/dotnet test  # Run NUnit tests
```

The plugin (`BehaviorAnalyzer.cs`) is deployed by copying to a Rust server's `oxide/plugins` directory.

## Architecture

```
Event Stream → Ingestion (MPSCQueue) → Feature Extraction → Peer Grouping → Detection → Risk Scoring → Output
```

### Backend Components

- **Core** (`include/core/`): Entity, Event, MetricVector, Anomaly, Explanation data structures
- **Detectors** (`include/detectors/`): ZScore, StreamingCUSUM, IsolationForest, Mahalanobis, PCA, OneClassSVM, EWMA, Seasonal, HoltWinters, ARIMA, Autoencoder
- **Pipeline** (`include/pipeline/`): StreamingRollingExtractor, CompositeFeatureExtractor for feature extraction
- **Scoring** (`include/scoring/`): StreamingRiskScorer with EMA-based accumulation
- **Domains** (`include/domains/`): Gaming (7 extractors) and HR (9 extractors) domain implementations
- **Ingestion** (`include/ingestion/`): Lock-free MPSCQueue for high-throughput event processing
- **Isolation** (`include/isolation/`): TenantScopedDetector, DistributedStateStore for multi-tenant isolation
- **Distributed** (`include/distributed/`): Production scaling infrastructure (see below)

### Frontend Structure

- React 19 + Vite 7 single-page application
- AWS Amplify for GraphQL API and DynamoDB storage
- Data model defined in `amplify/data/resource.ts`
- Ingest/auth middleware in `vite.config.js`
- Shared Lambda utilities in `amplify/functions/shared/`:
  - `rbac.ts` - Role-based access control (OWNER > ADMIN > ANALYST > VIEWER)
  - `logger.ts` - Structured logging with X-Request-ID tracing

### Rust Game Plugin

- C# Oxide/uMod plugin capturing server-side telemetry
- Hooks into gameplay events (combat, movement, looting, connections)
- Forwards data asynchronously to the frontend's GraphQL API
- Tests use mocked Rust/Oxide environment (`test/Stubs.cs`)

### Distributed Module

The `include/distributed/` module provides production-ready infrastructure for DynamoDB + Lambda architecture:

**AWS Client:**
- `dynamo_client.hh` - DynamoDB client for state storage
- `curl_dynamo_connection.hh` - Curl-based DynamoDB connection for LocalStack/AWS

**Production Readiness:**
- `tenant_resource_manager.hh` - Per-tenant quotas and rate limiting
- `audit_logger.hh` - Tamper-evident audit logging

**Observability:**
- `metrics.hh` - Prometheus metrics exposition
- `tracing.hh` - Distributed tracing (OpenTelemetry)
- `health.hh` - Health check endpoints

```bash
# Test distributed components
bazel test //test/distributed:all
```

## Key Patterns

### Adding a New Detector

1. Create header in `backend/include/detectors/`
2. Implement the `Detector` interface with `detect(sample, peers)` method
3. Add unit tests in `backend/test/`
4. Add benchmarks in `backend/benchmark/`

### Extending to a New Domain

1. Create feature extractors in `backend/include/domains/<domain>/`
2. Create a composite factory following the pattern in `gaming_composite_extractor.hh`
3. Add unit tests
4. Document features produced by each extractor

### Feature Naming Convention

Features use dot-separated hierarchical names: `<domain>.<category>.<metric>`
- `gaming.accuracy.session_mean`
- `hr.sentiment.trend`

## Build Configurations

The backend supports several Bazel configurations:
- `--config=debug`: Debug build with symbols
- `--config=release` or `-c opt`: Optimized release build
- `--config=asan`: Address sanitizer
- `--config=tsan`: Thread sanitizer
- `--config=coverage`: Code coverage instrumentation

## Performance Expectations (Release Mode)

| Operation | Throughput |
|-----------|------------|
| CUSUM update | >50M ops/sec |
| RunningStats update | >100M ops/sec |
| PeerNormalizer | >1M ops/sec |
| MPSCQueue (single thread) | >10M ops/sec |

## Distributed Deployment

For production multi-node deployments, see:

- **[Deployment Guide](backend/docs/DEPLOYMENT.md)**: AWS CDK infrastructure, ECS Fargate deployment, configuration
- **[Operations Guide](backend/docs/OPERATIONS.md)**: Monitoring, alerting, troubleshooting, maintenance

### Quick Architecture Overview

```
Rust Plugin ──► AppSync ──► Lambda ──► DynamoDB
```

The project uses a serverless architecture with DynamoDB + Lambda.

### Key Concepts

1. **Multi-Tenancy**: Per-tenant quotas and isolation via TenantResourceManager
2. **Audit Logging**: Tamper-evident audit trails for all operations

## Frontend-Backend Integration

The frontend (Amplify) uses DynamoDB + Lambda for data storage and processing.

### Architecture

```
Rust Plugin ──► POST /ingest ──► Batch Ingest Lambda ──► DynamoDB
                                                           │
                                                           ▼
                                              GraphQL API (AppSync)
                                                           │
                                                           ▼
                                              React Dashboard
```

### Local Development Setup

```bash
# Terminal 1: Start Amplify sandbox
cd frontend
npx ampx sandbox

# Terminal 2: Start frontend dev server
cd frontend
npm run dev

# Test the integration
curl -X POST http://localhost:5173/ingest \
  -H "Authorization: Bearer test" \
  -H "X-Server-Key: test" \
  -H "Content-Type: application/json" \
  -d '{"playerId":"p1","actionType":"WEAPON_FIRED","metadata":{"hits":1,"accuracy":0.85}}'
```

### Key Files

| File | Purpose |
|------|---------|
| `frontend/amplify/backend.ts` | Backend resource definitions |
| `frontend/amplify/data/resource.ts` | GraphQL schema and data models |
| `frontend/amplify/functions/batch-ingest/handler.ts` | Lambda for batch event ingestion |
