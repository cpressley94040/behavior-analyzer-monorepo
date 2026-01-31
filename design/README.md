# Design Documentation

This directory contains architecture decisions, system design documents, and technical specifications for the Behavior Analyzer platform.

## Documents

### [web_app.md](web_app.md)

High-level system requirements and architecture overview for the Behavior Analysis Web App.

**Contents:**
- Functional and non-functional requirements
- System architecture diagram
- Logical data model (UML)
- Data flow specifications

### [aws_architecture.md](aws_architecture.md)

AWS infrastructure implementation guide translating requirements into concrete cloud services.

**Contents:**
- Architecture overview with Mermaid diagrams
- Component details (Cognito, Amplify, API Gateway, Kinesis, DynamoDB, AppSync)
- Authentication and authorization patterns
- Data lake and operational database design

### [frontend_design.md](frontend_design.md)

Frontend architecture and user interface design specifications.

**Contents:**
- Technology stack (React, Vite, TypeScript, Amplify UI)
- Application structure and component hierarchy
- UI/UX flows (authentication, navigation, player profiles)
- Data integration patterns

### [rust_plugin.md](rust_plugin.md)

Design document for the Rust game server telemetry plugin.

**Contents:**
- Plugin architecture and responsibilities
- uMod hook integration details
- Event model and data schema
- Batching and retry strategies

### [backend_design.md](backend_design.md)

Backend C++ library design (placeholder for future documentation).

### [network_graph_visualization.md](network_graph_visualization.md)

Design document for the interactive account linkage graph visualization.

**Contents:**
- Technology selection (react-force-graph-2d)
- Graph data model (nodes, links)
- Visual design (colors, shapes, interactions)
- Component architecture
- Performance considerations
- Implementation plan (5 phases)

### [remaining_features.md](remaining_features.md)

Comprehensive design for features identified as gaps in the implementation assessment.

**Contents:**
- OpenSearch integration for complex forensic queries
- OAuth 2.0 implementation for Rust plugin
- WAF/CloudFront production security
- Dark mode theme system
- Export/Reports (PDF generation)

### [client_fingerprint_extension.md](client_fingerprint_extension.md)

Design for extending the Rust plugin's ClientFingerprint with hardware signals for ban evasion detection.

**Contents:**
- Available server-side APIs from Rust game assemblies
- Extended fingerprint structure with composite device hash
- Collection strategy (OnPlayerSetInfo buffering vs ClientInfo parsing)
- Backend IdentityDetector integration with DEVICE link type
- Privacy controls, rollout plan, and risk mitigations

### [IMPLEMENTATION_ASSESSMENT.md](IMPLEMENTATION_ASSESSMENT.md)

Living document tracking design vs implementation alignment.

**Contents:**
- Executive summary with alignment percentages
- Component-by-component analysis
- Completed features and remaining gaps
- Implementation completeness metrics

## Reading Order

For new contributors, we recommend reading the documents in this order:

1. **web_app.md** - Understand the high-level goals and requirements
2. **aws_architecture.md** - Learn how the system is deployed
3. **frontend_design.md** - Understand the user-facing application
4. **rust_plugin.md** - Learn about game telemetry capture
5. **backend_design.md** - Deep dive into the detection algorithms
6. **network_graph_visualization.md** - Graph visualization design
7. **remaining_features.md** - Upcoming feature designs
8. **IMPLEMENTATION_ASSESSMENT.md** - Current implementation status

## Architecture Overview

```
Game Servers          Web Application          Analysis Backend
     |                      |                        |
     v                      v                        v
+------------+      +---------------+      +------------------+
| rust_plugin| ---> | AWS Amplify   | ---> | C++ Detection    |
| (telemetry)|      | (Dashboard)   |      | Library (Bazel)  |
+------------+      +---------------+      +------------------+
     |                      |                        |
     v                      v                        v
  GraphQL API         DynamoDB             Anomaly Detection
  (Event Ingest)    (Event Storage)         (Statistical)
```

## Contributing

When adding new design documents:

1. Use Markdown format with clear headings
2. Include Mermaid diagrams where appropriate
3. Reference related documents with relative links
4. Keep documents focused on "what" and "why", not implementation details
5. Update this README with a summary of the new document
