# Behavior Analysis Web App – Design Document

**Primary Use Case (Iteration 1):**

Identify cheating and anomalous behavior in a multiplayer game (Rust)

**Long-Term Goal:**

A generic, reusable behavioral intelligence platform capable of ingesting arbitrary actions and detecting outliers, identity reuse, and coordinated behavior.

## 1. Functional Requirements

### Core Capabilities

1. Ingest arbitrary player actions
2. Detect statistical and behavioral outliers
3. Identify alternate / linked accounts
4. Support forensic investigations
5. Provide explainable evidence for every flag

### Non-Functional Requirements

* Horizontally scalable
* Immutable raw data
* Low-latency flagging (seconds–minutes)
* Replayable analytics
* Explainable ML decisions

## 2. System Architecture

### 2.1 High-Level Architecture

```
+-------------------+
| Rust Game Plugin  |
+-------------------+
          |
          v
+-------------------+
| API Gateway       |
+-------------------+
          |
          v
+-------------------+
| Kinesis Firehose  |
+-------------------+
     |          |
     |          +------------------+
     |                             |
     v                             v
+------------+             +------------------+
| S3 Raw     |             | Lambda Ingest    |
| Event Lake |             | Normalization    |
+------------+             +------------------+
                                   |
                                   v
                        +---------------------+
                        | DynamoDB (Hot Data) |
                        +---------------------+
                                   |
                    +--------------+--------------+
                    |                             |
                    v                             v
        +-----------------------+     +----------------------+
        | Glue / EMR / Spark    |     | Real-Time Detectors  |
        | Feature Pipelines     |     | (Lambda / KDA)       |
        +-----------------------+     +----------------------+
                    |                             |
                    v                             v
        +-----------------------+     +----------------------+
        | SageMaker Models      |     | DynamoDB Flags       |
        +-----------------------+     +----------------------+
                    |
                    v
        +-----------------------+
        | OpenSearch (Forensic) |
        +-----------------------+
                    |
                    v
        +-----------------------+
        | Web App (Amplify)     |
        +-----------------------+
```

## 3. Logical Data Model (UML)

### 3.1 Core Entities

```
+----------------+
| Player         |
+----------------+
| player_id (PK) |
| first_seen     |
| last_seen      |
| risk_score     |
+----------------+

+----------------+
| ActionEvent    |
+----------------+
| event_id (PK)  |
| player_id (FK) |
| action_type    |
| timestamp      |
| metadata       |
+----------------+

+----------------+
| Account        |
+----------------+
| account_id (PK)|
| created_at     |
+----------------+

+----------------+
| Flag           |
+----------------+
| flag_id (PK)   |
| player_id      |
| signal_type    |
| severity       |
| explanation    |
+----------------+
```

## 4. Event Ingestion & Normalization

### 4.1 Ingestion Contract

```json
{
  "event_id": "uuid",
  "player_id": "string",
  "account_id": "string",
  "session_id": "string",
  "action_type": "string",
  "timestamp": "unix_ms",
  "metadata": { "any": "json" },
  "client_fingerprint": {
    "ip_hash": "string",
    "hw_hash": "string",
    "os": "string"
  }
}
```

### 4.2 Normalization

* Validate timestamps
* Enrich with:
  * server_id
  * geo (from IP hash bucket)
* Drop malformed events (logged to DLQ)

## 5. DynamoDB Schema (Exact)

### 5.1 Table: `PlayerProfile`

**Purpose:** Player-level metadata and risk summary

| Field           | Type                 |
| --------------- | -------------------- |
| PK              | `PLAYER#{player_id}` |
| SK              | `PROFILE`            |
| account_id      | String               |
| first_seen      | Number               |
| last_seen       | Number               |
| lifetime_events | Number               |
| risk_score      | Number               |
| flags_open      | Number               |

**Access Patterns**

* Get player overview
* Update risk score

### 5.2 Table: `PlayerMetrics`

**Purpose:** Rolling aggregates for detection

| Field        | Type                            |
| ------------ | ------------------------------- |
| PK           | `PLAYER#{player_id}`            |
| SK           | `METRIC#{metric_name}#{window}` |
| count        | Number                          |
| mean         | Number                          |
| stddev       | Number                          |
| p95          | Number                          |
| last_updated | Number                          |

**Examples**

```
METRIC#HEADSHOT_RATE#7D
METRIC#ENGAGEMENT_DISTANCE_AK47#30D
```

### 5.3 Table: `PopulationMetrics`

**Purpose:** Baseline distributions

| Field       | Type                   |
| ----------- | ---------------------- |
| PK          | `POPULATION#{cohort}`  |
| SK          | `METRIC#{metric_name}` |
| mean        | Number                 |
| stddev      | Number                 |
| histogram   | List                   |
| sample_size | Number                 |

**Cohorts**

* GLOBAL
* WEAPON#AK47
* MAP#DESERT

### 5.4 Table: `Flags`

**Purpose:** Detection outputs (append-only)

| Field         | Type                        |
| ------------- | --------------------------- |
| PK            | `PLAYER#{player_id}`        |
| SK            | `FLAG#{timestamp}#{signal}` |
| signal        | String                      |
| severity      | Number                      |
| score         | Number                      |
| baseline      | String                      |
| explanation   | String                      |
| model_version | String                      |

### 5.5 Table: `AccountLinkSignals`

**Purpose:** Evidence for alt-account detection

| Field       | Type                      |
| ----------- | ------------------------- |
| PK          | `ENTITY#{entity_id}`      |
| SK          | `LINK#{linked_entity_id}` |
| signal_type | String                    |
| confidence  | Number                    |
| first_seen  | Number                    |
| last_seen   | Number                    |

## 6. ML Models by Signal

### 6.1 Statistical Outliers (MVP)

| Signal              | Model      | Reason      |
| ------------------- | ---------- | ----------- |
| Headshot rate       | Z-score    | Explainable |
| Accuracy            | Z-score    | Fast        |
| Engagement distance | Percentile | Non-normal  |

**Deployment**

* Lambda (real-time)
* No training required

### 6.2 Behavioral Outliers

#### Model: **Isolation Forest**

* Inputs:

  * accuracy
  * reaction time
  * distance variance
  * movement entropy
* Why:
  * Works with unlabeled data
  * Strong anomaly detection

**Used for**

* Soft-aim
* ESP-like awareness

### 6.3 Action Sequence Abuse

#### Model: **HMM → LSTM (later)**

**Phase 1**

* N-gram frequency + chi-square

**Phase 2**

* HMM per action category

**Phase 3**

* LSTM / Transformer (batch)

**Detects**

* Impossible action chains
* Bot-like repetition

### 6.4 Alternate Account Detection

#### Model Stack

| Signal              | Model                 |
| ------------------- | --------------------- |
| Hardware/IP reuse   | Graph scoring         |
| Skill ramp          | Bayesian change-point |
| Behavior similarity | Cosine similarity     |
| Sequence similarity | Dynamic Time Warping  |

**Final Score**

```
AltAccountScore =
  0.35 * HW/IP +
  0.30 * SkillRamp +
  0.20 * Behavior +
  0.15 * Sequence
```

### 6.5 Player Risk Score

```text
Risk = Σ (flag_severity × confidence × decay)
```

* Time-decayed
* Human-review friendly

## 7. Forensic Investigation Model

### Investigator Query Flow

```
Player → Flags → Evidence → Linked Accounts → Raw Events
```

### OpenSearch Indices

* `player_timeline`
* `player_interactions`
* `flags`
* `account_graph`

## 8. Security & Integrity

* Server-side ingestion only
* Signed payloads
* Event replay protection
* Drift monitoring
* Model versioning

## 9. Evolution Plan

### Phase 1

* Z-score
* Manual review
* Player timelines

### Phase 2

* Isolation Forest
* Alt-account detection
* Investigator dashboards

### Phase 3

* Automated enforcement
* Cross-game generalization
* Feedback-trained models

## 10. Why This Design Scales

* Raw data immutable
* Models replaceable
* Signals composable
* Evidence preserved
* Game-agnostic core

## Next Steps

If you want, I can:

* Generate **CloudFormation / CDK**
* Design **OpenSearch index mappings**
* Define **exact feature vectors per model**
* Write **review heuristics for human moderators**
* Help turn this into a **commercial anti-cheat SaaS**

Just tell me where to go next.
