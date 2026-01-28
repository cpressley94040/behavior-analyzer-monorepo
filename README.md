# Behavior Analyzer

A general-purpose behavioral outlier detection platform for streaming human behavior data. Designed to be domain-agnostic, explainable, and extensible.

## Overview

Behavior Analyzer processes streams of behavioral events, extracts features, compares against peer groups, and identifies statistically significant deviations. The platform is production-ready with 150+ unit tests, fuzz testing, and performance benchmarks.

**Key Design Principles:**
- **Domain-agnostic core** - Generic abstractions that work across any behavioral domain
- **Explainable detections** - Every anomaly includes contributing features and statistical context
- **Streaming-first** - Real-time processing with incremental updates
- **Peer-relative analysis** - Anomalies detected relative to comparable peer groups

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                    Event Stream (per-entity)                     │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                 Ingestion (MPSCQueue)                            │
│            Lock-free buffering per entity                        │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│              Feature Extraction Pipeline                         │
│   StreamingRollingExtractor → CompositeFeatureExtractor          │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                    Peer Grouping                                 │
│         Group entities, normalize with PeerNormalizer            │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                  Detection Pipeline                              │
│     ZScore, CUSUM, IsolationForest, SVM, EWMA, Autoencoder      │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                   Risk Scoring                                   │
│        StreamingRiskScorer (EMA-based accumulation)              │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│          Output: Anomalies + Explanations + Risk Score           │
└─────────────────────────────────────────────────────────────────┘
```

## Project Structure

```
behavior_analyzer/
├── backend/           # C++20 anomaly detection library (Bazel)
│   ├── src/server/    # Production server binary
│   ├── deploy/        # Dockerfile, docker-compose
│   └── config/        # YAML configuration files
├── frontend/          # React + Vite web dashboard (AWS Amplify)
├── games/rust_plugin/ # C# Oxide/uMod plugin for Rust game servers
├── kore/              # C++20 header-only data structures framework
├── infrastructure/    # AWS CDK infrastructure as code
│   └── cdk/           # ECS Fargate, Kinesis, DynamoDB, Redis
└── design/            # Architecture and design documentation
```

## Components

### Backend (C++20)

High-performance anomaly detection library with:
- **14+ detection algorithms**: ZScore, StreamingCUSUM, IsolationForest, Mahalanobis, PCA, One-Class SVM, EWMA, Seasonal, Holt-Winters, ARIMA, Autoencoder
- **Domain implementations**: Gaming (7 extractors), HR (9 extractors)
- **Lock-free ingestion**: MPSCQueue for high-throughput event processing
- **Explainable outputs**: Every detection includes contributing features and peer context

### Frontend (React)

Web dashboard for behavioral analysis:
- React 19 + Vite 7 single-page application
- AWS Amplify backend (GraphQL API, DynamoDB)
- Player investigation and anomaly visualization

### Rust Game Plugin (C#)

Oxide/uMod plugin for Rust dedicated servers:
- Captures server-side telemetry (combat, movement, looting, connections)
- Asynchronous event forwarding to the analysis API
- Steam ID tracking for player identification

### Kore Framework (C++20)

Header-only data structures library:
- Containers: Vector, Queue, Stack, HashTable, BST, B-Tree, RedBlackTree
- RNG: Mersenne Twister, Xoshiro256, XorShift with probability distributions
- Graph algorithms: Dijkstra, Kruskal MST, topological sort

## Quick Start

### Backend

```bash
cd backend

# Build library and server
bazel build //...
bazel build //src/server:behavior_analyzer_server

# Run all tests
bazel test //test:all

# Run benchmarks
bazel run -c opt //benchmark:all_bench

# Local development with Docker
cd deploy && docker-compose up
```

### Frontend

```bash
cd frontend
npm install
npm run dev    # http://localhost:5173
```

### AWS Deployment

```bash
cd infrastructure/cdk
npm install
cdk deploy --all    # Deploy ECS Fargate, Kinesis, DynamoDB, Redis
```

### Rust Plugin

```bash
cd games/rust_plugin/test
dotnet test
```

Deploy by copying `BehaviorAnalyzer.cs` to your Rust server's `oxide/plugins` directory.

## Detection Algorithms

| Detector | Type | Best For |
|----------|------|----------|
| ZScoreDetector | Statistical | Simple threshold-based detection |
| StreamingCUSUM | Change-point | Detecting sudden behavioral shifts |
| IsolationForest | Ensemble | High-dimensional data, no distribution assumptions |
| MahalanobisDetector | Multivariate | Correlated features |
| OneClassSVMDetector | Kernel SVM | Non-linear decision boundaries |
| EWMADetector | Time-series | Gradual drift detection |
| SeasonalDetector | Time-series | Periodic behavioral patterns |
| AutoencoderDetector | Neural network | Complex nonlinear patterns |

## Performance

Release mode benchmarks (Apple Silicon):

| Operation | Throughput |
|-----------|------------|
| CUSUM update | >50M ops/sec |
| RunningStats update | >100M ops/sec |
| PeerNormalizer | >1M ops/sec |
| MPSCQueue (single thread) | >10M ops/sec |

## Requirements

- **Backend**: Bazel 7.0+, C++20 compiler
- **Frontend**: Node.js 18+, npm
- **Rust Plugin**: .NET SDK 10.0+
- **Kore**: Bazel 7.0+, C++20 compiler

## Documentation

- [Backend README](backend/README.md) - Detailed API documentation with code examples
- [Frontend README](frontend/README.md) - Web application setup and data model
- [Kore README](kore/README.md) - Framework usage and container APIs
- [Games README](games/README.md) - Game integrations overview
- [Rust Plugin README](games/rust_plugin/README.md) - Plugin installation and configuration
- [Design Docs](design/README.md) - Architecture decisions and system design

## License

See individual component directories for licensing information.
