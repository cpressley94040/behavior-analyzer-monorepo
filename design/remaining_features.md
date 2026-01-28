# Remaining Features Design Document

This document provides detailed designs for the remaining gap items identified in the implementation assessment.

**Design Date:** 2026-01-18

---

## Table of Contents

1. [OpenSearch Integration](#1-opensearch-integration)
2. [OAuth Implementation for Rust Plugin](#2-oauth-implementation-for-rust-plugin)
3. [WAF/CloudFront Protection](#3-wafcloudfront-protection)
4. [Dark Mode Theme](#4-dark-mode-theme)
5. [Export/Reports (PDF)](#5-exportreports-pdf)

---

## 1. OpenSearch Integration

### 1.1 Purpose

Enable complex forensic queries across telemetry data that are impractical with DynamoDB's query patterns:
- Full-text search across event metadata
- Aggregations (time-series histograms, top-N queries)
- Fuzzy matching for player names
- Complex boolean queries across multiple fields
- Geospatial queries for IP-based analysis

### 1.2 Architecture

```
                                    ┌─────────────────────┐
                                    │   OpenSearch        │
                                    │   Domain            │
                                    │   ┌─────────────┐   │
┌──────────────┐    ┌──────────┐   │   │ telemetry-  │   │
│ DynamoDB     │───>│ Lambda   │──>│   │ events      │   │
│ Streams      │    │ Indexer  │   │   │ (Index)     │   │
└──────────────┘    └──────────┘   │   └─────────────┘   │
                                    │   ┌─────────────┐   │
                                    │   │ players     │   │
┌──────────────┐                   │   │ (Index)     │   │
│ Frontend     │<──────────────────│   └─────────────┘   │
│ (AppSync)    │                   │   ┌─────────────┐   │
└──────────────┘                   │   │ flags       │   │
                                    │   │ (Index)     │   │
                                    │   └─────────────┘   │
                                    └─────────────────────┘
```

### 1.3 Index Schemas

#### telemetry-events Index

```json
{
  "mappings": {
    "properties": {
      "eventId": { "type": "keyword" },
      "playerId": { "type": "keyword" },
      "sessionId": { "type": "keyword" },
      "actionType": { "type": "keyword" },
      "timestamp": { "type": "date" },
      "metadata": {
        "type": "object",
        "dynamic": true
      },
      "metadata_text": { "type": "text", "analyzer": "standard" },
      "fingerprint": {
        "properties": {
          "ipHash": { "type": "keyword" },
          "language": { "type": "keyword" }
        }
      },
      "owner": { "type": "keyword" }
    }
  },
  "settings": {
    "number_of_shards": 3,
    "number_of_replicas": 1,
    "index.lifecycle.name": "telemetry-policy",
    "index.lifecycle.rollover_alias": "telemetry-events"
  }
}
```

#### players Index

```json
{
  "mappings": {
    "properties": {
      "playerId": { "type": "keyword" },
      "owner": { "type": "keyword" },
      "riskScore": { "type": "float" },
      "status": { "type": "keyword" },
      "firstSeen": { "type": "date" },
      "lastSeen": { "type": "date" },
      "displayName": {
        "type": "text",
        "fields": {
          "keyword": { "type": "keyword" },
          "suggest": { "type": "completion" }
        }
      },
      "tags": { "type": "keyword" },
      "notes": { "type": "text" }
    }
  }
}
```

#### flags Index

```json
{
  "mappings": {
    "properties": {
      "flagId": { "type": "keyword" },
      "playerId": { "type": "keyword" },
      "owner": { "type": "keyword" },
      "signalType": { "type": "keyword" },
      "severity": { "type": "keyword" },
      "confidence": { "type": "float" },
      "status": { "type": "keyword" },
      "createdAt": { "type": "date" },
      "resolvedAt": { "type": "date" },
      "explanation": { "type": "text" }
    }
  }
}
```

### 1.4 CDK Infrastructure

```typescript
// amplify/custom/opensearch.ts
import * as opensearch from 'aws-cdk-lib/aws-opensearchservice';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as lambda from 'aws-cdk-lib/aws-lambda';

export function defineOpenSearch(backend: Backend) {
  const domain = new opensearch.Domain(backend.stack, 'ForensicSearch', {
    version: opensearch.EngineVersion.OPENSEARCH_2_11,
    domainName: 'behavior-analyzer-search',

    // Capacity
    capacity: {
      dataNodes: 2,
      dataNodeInstanceType: 't3.medium.search',
      masterNodes: 0, // Use data nodes for small deployments
    },

    // Storage
    ebs: {
      volumeSize: 100, // GB
      volumeType: ec2.EbsDeviceVolumeType.GP3,
    },

    // Security
    enforceHttps: true,
    nodeToNodeEncryption: true,
    encryptionAtRest: { enabled: true },

    // Access
    fineGrainedAccessControl: {
      masterUserArn: backend.auth.resources.authenticatedUserIamRole.roleArn,
    },

    // Lifecycle
    removalPolicy: RemovalPolicy.RETAIN,
  });

  // Index lifecycle policy (ILM equivalent)
  // Hot: 7 days, Warm: 30 days, Delete: 90 days

  return domain;
}
```

### 1.5 Indexer Lambda

```typescript
// amplify/functions/opensearch-indexer/handler.ts
import { DynamoDBStreamEvent } from 'aws-lambda';
import { Client } from '@opensearch-project/opensearch';
import { defaultProvider } from '@aws-sdk/credential-provider-node';
import { AwsSigv4Signer } from '@opensearch-project/opensearch/aws';

const client = new Client({
  ...AwsSigv4Signer({
    region: process.env.AWS_REGION!,
    service: 'es',
    getCredentials: () => defaultProvider()(),
  }),
  node: process.env.OPENSEARCH_ENDPOINT!,
});

export const handler = async (event: DynamoDBStreamEvent) => {
  const bulkBody: any[] = [];

  for (const record of event.Records) {
    if (record.eventName === 'INSERT' || record.eventName === 'MODIFY') {
      const item = unmarshall(record.dynamodb?.NewImage || {});

      // Determine index based on item type
      const index = getIndexName(item);

      bulkBody.push(
        { index: { _index: index, _id: item.id || item.eventId } },
        transformForSearch(item)
      );
    } else if (record.eventName === 'REMOVE') {
      const item = unmarshall(record.dynamodb?.OldImage || {});
      const index = getIndexName(item);

      bulkBody.push(
        { delete: { _index: index, _id: item.id || item.eventId } }
      );
    }
  }

  if (bulkBody.length > 0) {
    await client.bulk({ body: bulkBody, refresh: true });
  }
};

function getIndexName(item: any): string {
  if (item.actionType) return 'telemetry-events';
  if (item.signalType && item.severity) return 'flags';
  if (item.playerId && item.riskScore !== undefined) return 'players';
  return 'misc';
}

function transformForSearch(item: any): any {
  // Flatten metadata for full-text search
  if (item.metadata) {
    item.metadata_text = JSON.stringify(item.metadata);
  }
  return item;
}
```

### 1.6 AppSync Resolver for Search

```typescript
// amplify/data/search-resolver.ts
export const searchResolver = /* GraphQL */ `
  type SearchResult {
    hits: [SearchHit!]!
    total: Int!
    aggregations: AWSJSON
  }

  type SearchHit {
    id: String!
    index: String!
    score: Float!
    source: AWSJSON!
    highlight: AWSJSON
  }

  input SearchInput {
    query: String!
    index: String
    filters: AWSJSON
    from: Int
    size: Int
    sort: AWSJSON
    aggregations: AWSJSON
  }

  extend type Query {
    forensicSearch(input: SearchInput!): SearchResult!
      @auth(rules: [{ allow: owner }])
  }
`;
```

### 1.7 Frontend Search Component

```typescript
// src/components/forensic/ForensicSearch.tsx
import { useState } from 'react';
import {
  SearchField,
  Card,
  Flex,
  SelectField,
  SliderField,
  CheckboxField,
  Table,
  Pagination,
} from '@aws-amplify/ui-react';
import { generateClient } from 'aws-amplify/data';

interface SearchFilters {
  actionTypes: string[];
  dateRange: { start: Date; end: Date };
  minConfidence: number;
  playerIds: string[];
}

export const ForensicSearch = () => {
  const [query, setQuery] = useState('');
  const [filters, setFilters] = useState<SearchFilters>({
    actionTypes: [],
    dateRange: { start: new Date(Date.now() - 7 * 24 * 60 * 60 * 1000), end: new Date() },
    minConfidence: 0,
    playerIds: [],
  });
  const [results, setResults] = useState<SearchResult | null>(null);
  const [page, setPage] = useState(1);
  const pageSize = 25;

  const executeSearch = async () => {
    const client = generateClient();

    const searchInput = {
      query,
      index: 'telemetry-events',
      filters: {
        bool: {
          must: [
            query ? { multi_match: { query, fields: ['metadata_text', 'actionType'] } } : { match_all: {} },
          ],
          filter: [
            { range: { timestamp: { gte: filters.dateRange.start.toISOString(), lte: filters.dateRange.end.toISOString() } } },
            ...(filters.actionTypes.length > 0 ? [{ terms: { actionType: filters.actionTypes } }] : []),
            ...(filters.playerIds.length > 0 ? [{ terms: { playerId: filters.playerIds } }] : []),
          ],
        },
      },
      from: (page - 1) * pageSize,
      size: pageSize,
      sort: [{ timestamp: 'desc' }],
      aggregations: {
        action_types: { terms: { field: 'actionType', size: 20 } },
        timeline: { date_histogram: { field: 'timestamp', calendar_interval: 'hour' } },
      },
    };

    const result = await client.queries.forensicSearch({ input: searchInput });
    setResults(result.data);
  };

  return (
    <Card>
      <Flex direction="column" gap="1rem">
        <Flex gap="1rem" alignItems="flex-end">
          <SearchField
            label="Search Events"
            placeholder="weapon:rifle.ak AND distance:>100"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onSubmit={executeSearch}
            flex={1}
          />
          <SelectField
            label="Action Types"
            placeholder="All"
            isMultiple
            value={filters.actionTypes}
            onChange={(e) => setFilters({ ...filters, actionTypes: Array.from(e.target.selectedOptions, o => o.value) })}
          >
            <option value="PLAYER_ATTACK">Player Attack</option>
            <option value="PLAYER_DEATH">Player Death</option>
            <option value="SESSION_START">Session Start</option>
            <option value="SESSION_END">Session End</option>
          </SelectField>
        </Flex>

        {/* Results */}
        {results && (
          <>
            <Text>Found {results.total} results</Text>

            {/* Aggregation Charts */}
            {results.aggregations && (
              <TimelineHistogram data={results.aggregations.timeline.buckets} />
            )}

            {/* Results Table */}
            <Table highlightOnHover>
              {/* ... table content ... */}
            </Table>

            <Pagination
              currentPage={page}
              totalPages={Math.ceil(results.total / pageSize)}
              onChange={setPage}
            />
          </>
        )}
      </Flex>
    </Card>
  );
};
```

### 1.8 Example Queries

```json
// Find all headshots over 200m distance in last 24h
{
  "query": {
    "bool": {
      "must": [
        { "term": { "actionType": "PLAYER_ATTACK" } },
        { "term": { "metadata.IsHeadshot": true } },
        { "range": { "metadata.Distance": { "gte": 200 } } },
        { "range": { "timestamp": { "gte": "now-24h" } } }
      ]
    }
  },
  "aggs": {
    "by_player": {
      "terms": { "field": "playerId", "size": 10 }
    }
  }
}

// Find players with similar IP hashes (potential alts)
{
  "query": {
    "term": { "fingerprint.ipHash": "abc123..." }
  },
  "aggs": {
    "unique_players": {
      "cardinality": { "field": "playerId" }
    }
  }
}

// Full-text search across metadata
{
  "query": {
    "multi_match": {
      "query": "rifle headshot suspicious",
      "fields": ["metadata_text", "actionType"],
      "fuzziness": "AUTO"
    }
  }
}
```

---

## 2. OAuth Implementation for Rust Plugin

### 2.1 Purpose

Replace static API key authentication with OAuth 2.0 Client Credentials flow for:
- Token rotation without plugin reload
- Fine-grained scope control
- Audit trail of server authentication
- Centralized credential management

### 2.2 Architecture

```
┌─────────────────┐         ┌─────────────────┐
│   Rust Server   │         │    Cognito      │
│   (Plugin)      │         │   User Pool     │
└────────┬────────┘         └────────┬────────┘
         │                           │
         │ 1. POST /oauth2/token     │
         │    client_credentials     │
         │ ─────────────────────────>│
         │                           │
         │ 2. access_token (JWT)     │
         │ <─────────────────────────│
         │                           │
         │ 3. POST /ingest           │
         │    Authorization: Bearer  │
         │ ─────────────────────────>│ API Gateway
         │                           │ (validates JWT)
         │ 4. 200 OK                 │
         │ <─────────────────────────│
         │                           │
```

### 2.3 Cognito App Client Configuration

```typescript
// amplify/auth/resource.ts
import { defineAuth } from '@aws-amplify/backend';

export const auth = defineAuth({
  loginWith: {
    email: true,
  },

  // Machine-to-machine clients for game servers
  access: (allow) => [
    allow.resource(data).to(['mutate', 'query']),
  ],
});

// Custom CDK for M2M client
export function addM2MClient(backend: Backend) {
  const userPool = backend.auth.resources.userPool;

  // Resource server for ingest scope
  const resourceServer = new cognito.CfnUserPoolResourceServer(
    backend.stack,
    'IngestResourceServer',
    {
      userPoolId: userPool.userPoolId,
      identifier: 'ingest',
      name: 'Telemetry Ingestion',
      scopes: [
        { scopeName: 'write', scopeDescription: 'Submit telemetry events' },
        { scopeName: 'read', scopeDescription: 'Read player data' },
      ],
    }
  );

  // M2M App Client
  const m2mClient = new cognito.CfnUserPoolClient(
    backend.stack,
    'GameServerM2MClient',
    {
      userPoolId: userPool.userPoolId,
      clientName: 'game-server-m2m',
      generateSecret: true,
      allowedOAuthFlows: ['client_credentials'],
      allowedOAuthScopes: ['ingest/write', 'ingest/read'],
      allowedOAuthFlowsUserPoolClient: true,
      supportedIdentityProviders: ['COGNITO'],
    }
  );

  m2mClient.addDependency(resourceServer);

  // Output for plugin configuration
  backend.addOutput({
    custom: {
      oauthTokenUrl: `https://${userPool.userPoolProviderUrl}/oauth2/token`,
      oauthClientId: m2mClient.ref,
    },
  });
}
```

### 2.4 Plugin OAuth Implementation

```csharp
// BehaviorAnalyzer.cs - OAuth Module

public class OAuthManager
{
    private readonly BehaviorAnalyzer plugin;
    private string accessToken;
    private DateTime tokenExpiry;
    private readonly object tokenLock = new object();
    private bool isRefreshing = false;

    public OAuthManager(BehaviorAnalyzer plugin)
    {
        this.plugin = plugin;
    }

    public bool IsConfigured =>
        !string.IsNullOrEmpty(plugin.config.OAuthClientId) &&
        !string.IsNullOrEmpty(plugin.config.OAuthClientSecret) &&
        !string.IsNullOrEmpty(plugin.config.OAuthTokenUrl);

    public bool IsTokenValid =>
        !string.IsNullOrEmpty(accessToken) &&
        DateTime.UtcNow < tokenExpiry;

    public string GetAuthorizationHeader()
    {
        if (!IsConfigured)
        {
            // Fallback to API key
            return string.IsNullOrEmpty(plugin.config.ApiKey)
                ? null
                : $"X-API-Key: {plugin.config.ApiKey}";
        }

        if (!IsTokenValid && !isRefreshing)
        {
            RefreshToken();
        }

        return IsTokenValid ? $"Bearer {accessToken}" : null;
    }

    public void RefreshToken()
    {
        lock (tokenLock)
        {
            if (isRefreshing) return;
            isRefreshing = true;
        }

        var formData = new Dictionary<string, string>
        {
            { "grant_type", "client_credentials" },
            { "client_id", plugin.config.OAuthClientId },
            { "client_secret", plugin.config.OAuthClientSecret },
            { "scope", "ingest/write ingest/read" }
        };

        // URL-encode the form data
        string body = string.Join("&", formData.Select(kvp =>
            $"{Uri.EscapeDataString(kvp.Key)}={Uri.EscapeDataString(kvp.Value)}"));

        var headers = new Dictionary<string, string>
        {
            { "Content-Type", "application/x-www-form-urlencoded" }
        };

        plugin.webrequest.Enqueue(
            plugin.config.OAuthTokenUrl,
            body,
            (code, response) => HandleTokenResponse(code, response),
            plugin,
            RequestMethod.POST,
            headers
        );
    }

    private void HandleTokenResponse(int code, string response)
    {
        try
        {
            if (code == 200)
            {
                var tokenResponse = JsonConvert.DeserializeObject<OAuthTokenResponse>(response);

                lock (tokenLock)
                {
                    accessToken = tokenResponse.access_token;
                    // Refresh 60 seconds before expiry
                    tokenExpiry = DateTime.UtcNow.AddSeconds(tokenResponse.expires_in - 60);
                    isRefreshing = false;
                }

                plugin.Puts($"OAuth token refreshed, expires in {tokenResponse.expires_in}s");

                // Schedule next refresh
                float refreshIn = Math.Max(tokenResponse.expires_in - 120, 60);
                plugin.timer.Once(refreshIn, RefreshToken);
            }
            else
            {
                plugin.PrintError($"OAuth token refresh failed: {code} - {response}");
                lock (tokenLock) { isRefreshing = false; }

                // Retry with backoff
                plugin.timer.Once(30f, RefreshToken);
            }
        }
        catch (Exception ex)
        {
            plugin.PrintError($"OAuth parse error: {ex.Message}");
            lock (tokenLock) { isRefreshing = false; }
        }
    }
}

public class OAuthTokenResponse
{
    public string access_token { get; set; }
    public string token_type { get; set; }
    public int expires_in { get; set; }
    public string scope { get; set; }
}
```

### 2.5 Updated HTTP Request with OAuth

```csharp
void SendBatch(List<TelemetryEvent> events, int retryCount = 0)
{
    string json = JsonConvert.SerializeObject(events);

    var headers = new Dictionary<string, string>
    {
        { "Content-Type", "application/json" }
    };

    // Get authorization from OAuth manager
    string authHeader = oauthManager.GetAuthorizationHeader();
    if (authHeader == null)
    {
        PrintWarning("No valid authentication available, queueing batch for retry");
        timer.Once(5f, () => SendBatch(events, retryCount));
        return;
    }

    if (authHeader.StartsWith("Bearer"))
    {
        headers["Authorization"] = authHeader;
    }
    else if (authHeader.StartsWith("X-API-Key"))
    {
        headers["X-API-Key"] = authHeader.Substring(10);
    }

    webrequest.Enqueue(
        config.Endpoint,
        json,
        (code, response) => HandleBatchResponse(code, response, events, retryCount),
        this,
        RequestMethod.POST,
        headers
    );
}

void HandleBatchResponse(int code, string response, List<TelemetryEvent> events, int retryCount)
{
    if (code == 200 || code == 202)
    {
        Puts($"Successfully sent {events.Count} events");
    }
    else if (code == 401)
    {
        // Token expired or invalid, force refresh
        PrintWarning("Authentication failed, refreshing token");
        oauthManager.RefreshToken();

        // Retry after token refresh
        timer.Once(2f, () => SendBatch(events, retryCount));
    }
    else if (code >= 500 && retryCount < config.MaxRetries)
    {
        float delay = config.RetryDelayMs / 1000f * (float)Math.Pow(2, retryCount);
        PrintWarning($"Server error {code}, retrying in {delay}s");
        timer.Once(delay, () => SendBatch(events, retryCount + 1));
    }
    else
    {
        PrintError($"Failed to send batch: {code} - {response}");
    }
}
```

### 2.6 Configuration Update

```json
{
  "Endpoint": "https://api.example.com/ingest",
  "ApiKey": "",

  "OAuthClientId": "abc123...",
  "OAuthClientSecret": "secret...",
  "OAuthTokenUrl": "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_xxx/oauth2/token",
  "OAuthScopes": "ingest/write ingest/read",

  "EnabledEvents": ["PLAYER_HIT", "PLAYER_KILLED"],
  "BatchSize": 50,
  "FlushIntervalMs": 2000
}
```

---

## 3. WAF/CloudFront Protection

### 3.1 Purpose

Production-grade security for:
- DDoS protection via AWS Shield
- Rate limiting to prevent abuse
- Bot detection and blocking
- Geo-restriction capabilities
- SQL injection / XSS prevention

### 3.2 Architecture

```
                                    ┌──────────────┐
                                    │   AWS WAF    │
                                    │   WebACL     │
                                    └──────┬───────┘
                                           │
┌──────────┐    ┌──────────────┐    ┌──────┴───────┐    ┌──────────────┐
│  Client  │───>│  CloudFront  │───>│ API Gateway  │───>│   Backend    │
│          │    │  (Shield)    │    │              │    │   Services   │
└──────────┘    └──────────────┘    └──────────────┘    └──────────────┘
                      │
                      v
               ┌──────────────┐
               │   S3 Origin  │
               │ (Frontend)   │
               └──────────────┘
```

### 3.3 CDK Infrastructure

```typescript
// amplify/custom/security.ts
import * as wafv2 from 'aws-cdk-lib/aws-wafv2';
import * as cloudfront from 'aws-cdk-lib/aws-cloudfront';
import * as origins from 'aws-cdk-lib/aws-cloudfront-origins';

export function defineSecurityStack(backend: Backend) {
  // WAF WebACL
  const webAcl = new wafv2.CfnWebACL(backend.stack, 'BehaviorAnalyzerWAF', {
    name: 'behavior-analyzer-waf',
    scope: 'REGIONAL', // Use CLOUDFRONT for CloudFront
    defaultAction: { allow: {} },

    visibilityConfig: {
      cloudWatchMetricsEnabled: true,
      metricName: 'BehaviorAnalyzerWAF',
      sampledRequestsEnabled: true,
    },

    rules: [
      // Rate limiting rule
      {
        name: 'RateLimitRule',
        priority: 1,
        action: { block: {} },
        statement: {
          rateBasedStatement: {
            limit: 2000, // requests per 5 minutes
            aggregateKeyType: 'IP',
          },
        },
        visibilityConfig: {
          cloudWatchMetricsEnabled: true,
          metricName: 'RateLimitRule',
          sampledRequestsEnabled: true,
        },
      },

      // AWS Managed Rules - Common Rule Set
      {
        name: 'AWSManagedRulesCommonRuleSet',
        priority: 2,
        overrideAction: { none: {} },
        statement: {
          managedRuleGroupStatement: {
            vendorName: 'AWS',
            name: 'AWSManagedRulesCommonRuleSet',
          },
        },
        visibilityConfig: {
          cloudWatchMetricsEnabled: true,
          metricName: 'CommonRuleSet',
          sampledRequestsEnabled: true,
        },
      },

      // AWS Managed Rules - Known Bad Inputs
      {
        name: 'AWSManagedRulesKnownBadInputsRuleSet',
        priority: 3,
        overrideAction: { none: {} },
        statement: {
          managedRuleGroupStatement: {
            vendorName: 'AWS',
            name: 'AWSManagedRulesKnownBadInputsRuleSet',
          },
        },
        visibilityConfig: {
          cloudWatchMetricsEnabled: true,
          metricName: 'KnownBadInputs',
          sampledRequestsEnabled: true,
        },
      },

      // AWS Managed Rules - SQL Injection
      {
        name: 'AWSManagedRulesSQLiRuleSet',
        priority: 4,
        overrideAction: { none: {} },
        statement: {
          managedRuleGroupStatement: {
            vendorName: 'AWS',
            name: 'AWSManagedRulesSQLiRuleSet',
          },
        },
        visibilityConfig: {
          cloudWatchMetricsEnabled: true,
          metricName: 'SQLiRuleSet',
          sampledRequestsEnabled: true,
        },
      },

      // Bot Control (optional, additional cost)
      {
        name: 'AWSManagedRulesBotControlRuleSet',
        priority: 5,
        overrideAction: { none: {} },
        statement: {
          managedRuleGroupStatement: {
            vendorName: 'AWS',
            name: 'AWSManagedRulesBotControlRuleSet',
          },
        },
        visibilityConfig: {
          cloudWatchMetricsEnabled: true,
          metricName: 'BotControl',
          sampledRequestsEnabled: true,
        },
      },

      // Geo-restriction (block specific countries if needed)
      {
        name: 'GeoBlockRule',
        priority: 6,
        action: { block: {} },
        statement: {
          geoMatchStatement: {
            countryCodes: [], // Add country codes to block, e.g., ['CN', 'RU']
          },
        },
        visibilityConfig: {
          cloudWatchMetricsEnabled: true,
          metricName: 'GeoBlock',
          sampledRequestsEnabled: true,
        },
      },

      // Custom rule for ingest endpoint rate limiting
      {
        name: 'IngestEndpointRateLimit',
        priority: 7,
        action: { block: {} },
        statement: {
          rateBasedStatement: {
            limit: 10000, // Higher limit for game servers
            aggregateKeyType: 'IP',
            scopeDownStatement: {
              byteMatchStatement: {
                fieldToMatch: { uriPath: {} },
                positionalConstraint: 'STARTS_WITH',
                searchString: '/ingest',
                textTransformations: [{ priority: 0, type: 'LOWERCASE' }],
              },
            },
          },
        },
        visibilityConfig: {
          cloudWatchMetricsEnabled: true,
          metricName: 'IngestRateLimit',
          sampledRequestsEnabled: true,
        },
      },
    ],
  });

  // Associate WAF with API Gateway
  new wafv2.CfnWebACLAssociation(backend.stack, 'WAFApiGatewayAssoc', {
    resourceArn: backend.data.resources.graphqlApi.arn,
    webAclArn: webAcl.attrArn,
  });

  return webAcl;
}
```

### 3.4 CloudFront Distribution

```typescript
// amplify/custom/cloudfront.ts
export function defineCloudFront(backend: Backend) {
  // S3 bucket for frontend (created by Amplify Hosting)
  const s3Origin = new origins.S3Origin(backend.hosting.resources.bucket);

  // API Gateway origin
  const apiOrigin = new origins.HttpOrigin(
    `${backend.data.resources.graphqlApi.graphqlUrl.replace('https://', '')}`,
    {
      protocolPolicy: cloudfront.OriginProtocolPolicy.HTTPS_ONLY,
    }
  );

  const distribution = new cloudfront.Distribution(backend.stack, 'CDN', {
    defaultBehavior: {
      origin: s3Origin,
      viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
      cachePolicy: cloudfront.CachePolicy.CACHING_OPTIMIZED,
      responseHeadersPolicy: cloudfront.ResponseHeadersPolicy.SECURITY_HEADERS,
    },

    additionalBehaviors: {
      '/graphql': {
        origin: apiOrigin,
        viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.HTTPS_ONLY,
        cachePolicy: cloudfront.CachePolicy.CACHING_DISABLED,
        originRequestPolicy: cloudfront.OriginRequestPolicy.ALL_VIEWER_EXCEPT_HOST_HEADER,
        allowedMethods: cloudfront.AllowedMethods.ALLOW_ALL,
      },
      '/ingest': {
        origin: apiOrigin,
        viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.HTTPS_ONLY,
        cachePolicy: cloudfront.CachePolicy.CACHING_DISABLED,
        originRequestPolicy: cloudfront.OriginRequestPolicy.ALL_VIEWER_EXCEPT_HOST_HEADER,
        allowedMethods: cloudfront.AllowedMethods.ALLOW_ALL,
      },
    },

    // Security
    minimumProtocolVersion: cloudfront.SecurityPolicyProtocol.TLS_V1_2_2021,

    // Error pages
    errorResponses: [
      {
        httpStatus: 403,
        responseHttpStatus: 200,
        responsePagePath: '/index.html',
        ttl: Duration.seconds(0),
      },
      {
        httpStatus: 404,
        responseHttpStatus: 200,
        responsePagePath: '/index.html',
        ttl: Duration.seconds(0),
      },
    ],

    // Logging
    enableLogging: true,
    logBucket: new s3.Bucket(backend.stack, 'CDNLogs', {
      lifecycleRules: [{ expiration: Duration.days(30) }],
    }),
  });

  return distribution;
}
```

### 3.5 Security Headers

```typescript
// Response headers policy
const securityHeadersPolicy = new cloudfront.ResponseHeadersPolicy(
  backend.stack,
  'SecurityHeaders',
  {
    securityHeadersBehavior: {
      contentSecurityPolicy: {
        contentSecurityPolicy: [
          "default-src 'self'",
          "script-src 'self' 'unsafe-inline'",
          "style-src 'self' 'unsafe-inline'",
          "img-src 'self' data: https:",
          "connect-src 'self' https://*.amazonaws.com wss://*.amazonaws.com",
          "font-src 'self'",
          "frame-ancestors 'none'",
        ].join('; '),
        override: true,
      },
      strictTransportSecurity: {
        accessControlMaxAge: Duration.days(365),
        includeSubdomains: true,
        preload: true,
        override: true,
      },
      contentTypeOptions: { override: true },
      frameOptions: {
        frameOption: cloudfront.HeadersFrameOption.DENY,
        override: true,
      },
      referrerPolicy: {
        referrerPolicy: cloudfront.HeadersReferrerPolicy.STRICT_ORIGIN_WHEN_CROSS_ORIGIN,
        override: true,
      },
      xssProtection: {
        protection: true,
        modeBlock: true,
        override: true,
      },
    },
  }
);
```

---

## 4. Dark Mode Theme

### 4.1 Purpose

Provide user preference for light/dark theme:
- Reduce eye strain during extended use
- Match system preferences
- Persist preference across sessions

### 4.2 Implementation Approach

Use AWS Amplify UI's built-in theming system with CSS custom properties.

### 4.3 Theme Definition

```typescript
// src/theme/index.ts
import { createTheme, defaultDarkModeOverride } from '@aws-amplify/ui-react';

export const lightTheme = createTheme({
  name: 'behavior-analyzer-light',
  tokens: {
    colors: {
      background: {
        primary: { value: '#ffffff' },
        secondary: { value: '#f8fafc' },
        tertiary: { value: '#f1f5f9' },
      },
      font: {
        primary: { value: '#0f172a' },
        secondary: { value: '#475569' },
        tertiary: { value: '#94a3b8' },
      },
      border: {
        primary: { value: '#e2e8f0' },
        secondary: { value: '#cbd5e1' },
      },
      brand: {
        primary: {
          10: { value: '#eff6ff' },
          20: { value: '#dbeafe' },
          40: { value: '#93c5fd' },
          60: { value: '#3b82f6' },
          80: { value: '#1d4ed8' },
          90: { value: '#1e40af' },
          100: { value: '#1e3a8a' },
        },
      },
      // Risk score colors
      red: {
        10: { value: '#fef2f2' },
        60: { value: '#dc2626' },
        80: { value: '#991b1b' },
      },
      orange: {
        10: { value: '#fff7ed' },
        60: { value: '#ea580c' },
      },
      green: {
        10: { value: '#f0fdf4' },
        60: { value: '#16a34a' },
      },
    },
    shadows: {
      small: { value: '0 1px 2px 0 rgb(0 0 0 / 0.05)' },
      medium: { value: '0 4px 6px -1px rgb(0 0 0 / 0.1)' },
      large: { value: '0 10px 15px -3px rgb(0 0 0 / 0.1)' },
    },
    radii: {
      small: { value: '4px' },
      medium: { value: '8px' },
      large: { value: '12px' },
    },
  },
});

export const darkTheme = createTheme({
  name: 'behavior-analyzer-dark',
  tokens: {
    colors: {
      background: {
        primary: { value: '#0f172a' },
        secondary: { value: '#1e293b' },
        tertiary: { value: '#334155' },
      },
      font: {
        primary: { value: '#f8fafc' },
        secondary: { value: '#cbd5e1' },
        tertiary: { value: '#64748b' },
      },
      border: {
        primary: { value: '#334155' },
        secondary: { value: '#475569' },
      },
      brand: {
        primary: {
          10: { value: '#1e3a8a' },
          20: { value: '#1e40af' },
          40: { value: '#2563eb' },
          60: { value: '#3b82f6' },
          80: { value: '#60a5fa' },
          90: { value: '#93c5fd' },
          100: { value: '#dbeafe' },
        },
      },
      red: {
        10: { value: '#450a0a' },
        60: { value: '#ef4444' },
        80: { value: '#fca5a5' },
      },
      orange: {
        10: { value: '#431407' },
        60: { value: '#f97316' },
      },
      green: {
        10: { value: '#052e16' },
        60: { value: '#22c55e' },
      },
    },
    shadows: {
      small: { value: '0 1px 2px 0 rgb(0 0 0 / 0.3)' },
      medium: { value: '0 4px 6px -1px rgb(0 0 0 / 0.4)' },
      large: { value: '0 10px 15px -3px rgb(0 0 0 / 0.5)' },
    },
  },
});
```

### 4.4 Theme Context

```typescript
// src/context/ThemeContext.tsx
import { createContext, useContext, useState, useEffect, ReactNode } from 'react';
import { ThemeProvider as AmplifyThemeProvider, ColorMode } from '@aws-amplify/ui-react';
import { lightTheme, darkTheme } from '../theme';

type ThemeMode = 'light' | 'dark' | 'system';

interface ThemeContextType {
  mode: ThemeMode;
  setMode: (mode: ThemeMode) => void;
  resolvedMode: 'light' | 'dark';
}

const ThemeContext = createContext<ThemeContextType | null>(null);

const STORAGE_KEY = 'behavior-analyzer-theme';

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [mode, setModeState] = useState<ThemeMode>(() => {
    const stored = localStorage.getItem(STORAGE_KEY);
    return (stored as ThemeMode) || 'system';
  });

  const [systemPreference, setSystemPreference] = useState<'light' | 'dark'>(() =>
    window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
  );

  // Listen for system preference changes
  useEffect(() => {
    const mediaQuery = window.matchMedia('(prefers-color-scheme: dark)');
    const handler = (e: MediaQueryListEvent) => {
      setSystemPreference(e.matches ? 'dark' : 'light');
    };
    mediaQuery.addEventListener('change', handler);
    return () => mediaQuery.removeEventListener('change', handler);
  }, []);

  const setMode = (newMode: ThemeMode) => {
    setModeState(newMode);
    localStorage.setItem(STORAGE_KEY, newMode);
  };

  const resolvedMode = mode === 'system' ? systemPreference : mode;
  const theme = resolvedMode === 'dark' ? darkTheme : lightTheme;

  // Apply to document for non-Amplify components
  useEffect(() => {
    document.documentElement.setAttribute('data-theme', resolvedMode);
    document.documentElement.style.colorScheme = resolvedMode;
  }, [resolvedMode]);

  return (
    <ThemeContext.Provider value={{ mode, setMode, resolvedMode }}>
      <AmplifyThemeProvider theme={theme} colorMode={resolvedMode as ColorMode}>
        {children}
      </AmplifyThemeProvider>
    </ThemeContext.Provider>
  );
}

export function useTheme() {
  const context = useContext(ThemeContext);
  if (!context) {
    throw new Error('useTheme must be used within ThemeProvider');
  }
  return context;
}
```

### 4.5 Theme Toggle Component

```typescript
// src/components/common/ThemeToggle.tsx
import { ToggleButtonGroup, ToggleButton, Flex, Text } from '@aws-amplify/ui-react';
import { Sun, Moon, Monitor } from 'lucide-react';
import { useTheme } from '../../context/ThemeContext';

export function ThemeToggle() {
  const { mode, setMode } = useTheme();

  return (
    <Flex direction="column" gap="0.5rem">
      <Text fontSize="0.875rem" fontWeight="500">
        Theme
      </Text>
      <ToggleButtonGroup
        value={mode}
        isExclusive
        onChange={(value) => setMode(value as 'light' | 'dark' | 'system')}
        size="small"
      >
        <ToggleButton value="light" aria-label="Light mode">
          <Flex alignItems="center" gap="0.25rem">
            <Sun size={14} />
            Light
          </Flex>
        </ToggleButton>
        <ToggleButton value="dark" aria-label="Dark mode">
          <Flex alignItems="center" gap="0.25rem">
            <Moon size={14} />
            Dark
          </Flex>
        </ToggleButton>
        <ToggleButton value="system" aria-label="System preference">
          <Flex alignItems="center" gap="0.25rem">
            <Monitor size={14} />
            System
          </Flex>
        </ToggleButton>
      </ToggleButtonGroup>
    </Flex>
  );
}
```

### 4.6 Quick Toggle (Header)

```typescript
// src/components/layout/ThemeQuickToggle.tsx
import { Button } from '@aws-amplify/ui-react';
import { Sun, Moon } from 'lucide-react';
import { useTheme } from '../../context/ThemeContext';

export function ThemeQuickToggle() {
  const { resolvedMode, setMode } = useTheme();

  const toggle = () => {
    setMode(resolvedMode === 'light' ? 'dark' : 'light');
  };

  return (
    <Button
      variation="link"
      onClick={toggle}
      aria-label={`Switch to ${resolvedMode === 'light' ? 'dark' : 'light'} mode`}
      padding="0.5rem"
    >
      {resolvedMode === 'light' ? <Moon size={20} /> : <Sun size={20} />}
    </Button>
  );
}
```

### 4.7 App Integration

```typescript
// src/App.tsx
import { Authenticator } from '@aws-amplify/ui-react';
import { ThemeProvider } from './context/ThemeContext';
import { Layout } from './components/layout/Layout';

export function App() {
  return (
    <ThemeProvider>
      <Authenticator>
        {({ signOut, user }) => (
          <Layout signOut={signOut} user={user}>
            {/* Routes */}
          </Layout>
        )}
      </Authenticator>
    </ThemeProvider>
  );
}
```

### 4.8 CSS Variables for Custom Components

```css
/* src/styles/theme.css */
:root[data-theme='light'] {
  --graph-background: #f8fafc;
  --graph-node-player: #3b82f6;
  --graph-node-ip: #22c55e;
  --graph-node-hwid: #a855f7;
  --graph-node-session: #f59e0b;
  --graph-link-default: #94a3b8;
  --graph-text: #0f172a;
}

:root[data-theme='dark'] {
  --graph-background: #1e293b;
  --graph-node-player: #60a5fa;
  --graph-node-ip: #4ade80;
  --graph-node-hwid: #c084fc;
  --graph-node-session: #fbbf24;
  --graph-link-default: #64748b;
  --graph-text: #f8fafc;
}
```

---

## 5. Export/Reports (PDF)

### 5.1 Purpose

Enable investigators to generate downloadable reports:
- Player investigation summaries
- Flag review documentation
- Evidence packages for ban appeals
- Audit trail exports

### 5.2 Technology Stack

- **PDF Generation**: `@react-pdf/renderer` (client-side) or Lambda + `puppeteer` (server-side)
- **Charts in PDF**: `recharts` with `recharts-to-png` for image conversion
- **Styling**: Tailwind-like utility classes via react-pdf

### 5.3 Report Types

| Report Type | Contents | Use Case |
|-------------|----------|----------|
| Player Summary | Profile, risk score, flags, connections | Quick overview |
| Investigation Report | Full timeline, evidence, investigator notes | Ban documentation |
| Flag Batch Export | Multiple flags with context | Bulk review |
| Audit Log | All actions taken on a player | Compliance |

### 5.4 PDF Document Structure

```typescript
// src/components/reports/PlayerReportDocument.tsx
import {
  Document,
  Page,
  Text,
  View,
  StyleSheet,
  Image,
  Font,
} from '@react-pdf/renderer';

// Register fonts
Font.register({
  family: 'Inter',
  fonts: [
    { src: '/fonts/Inter-Regular.ttf', fontWeight: 400 },
    { src: '/fonts/Inter-Medium.ttf', fontWeight: 500 },
    { src: '/fonts/Inter-Bold.ttf', fontWeight: 700 },
  ],
});

const styles = StyleSheet.create({
  page: {
    fontFamily: 'Inter',
    fontSize: 10,
    padding: 40,
    backgroundColor: '#ffffff',
  },
  header: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    marginBottom: 20,
    paddingBottom: 10,
    borderBottomWidth: 1,
    borderBottomColor: '#e2e8f0',
  },
  title: {
    fontSize: 24,
    fontWeight: 700,
    color: '#0f172a',
  },
  subtitle: {
    fontSize: 12,
    color: '#64748b',
    marginTop: 4,
  },
  section: {
    marginBottom: 20,
  },
  sectionTitle: {
    fontSize: 14,
    fontWeight: 700,
    color: '#0f172a',
    marginBottom: 10,
    paddingBottom: 5,
    borderBottomWidth: 1,
    borderBottomColor: '#e2e8f0',
  },
  row: {
    flexDirection: 'row',
    marginBottom: 4,
  },
  label: {
    width: 120,
    color: '#64748b',
    fontWeight: 500,
  },
  value: {
    flex: 1,
    color: '#0f172a',
  },
  badge: {
    paddingHorizontal: 8,
    paddingVertical: 2,
    borderRadius: 4,
    fontSize: 9,
    fontWeight: 500,
  },
  badgeRed: {
    backgroundColor: '#fef2f2',
    color: '#dc2626',
  },
  badgeYellow: {
    backgroundColor: '#fefce8',
    color: '#ca8a04',
  },
  badgeGreen: {
    backgroundColor: '#f0fdf4',
    color: '#16a34a',
  },
  table: {
    marginTop: 10,
  },
  tableHeader: {
    flexDirection: 'row',
    backgroundColor: '#f8fafc',
    paddingVertical: 6,
    paddingHorizontal: 8,
    borderBottomWidth: 1,
    borderBottomColor: '#e2e8f0',
  },
  tableHeaderCell: {
    fontWeight: 700,
    color: '#475569',
    fontSize: 9,
  },
  tableRow: {
    flexDirection: 'row',
    paddingVertical: 6,
    paddingHorizontal: 8,
    borderBottomWidth: 1,
    borderBottomColor: '#f1f5f9',
  },
  tableCell: {
    fontSize: 9,
    color: '#0f172a',
  },
  footer: {
    position: 'absolute',
    bottom: 30,
    left: 40,
    right: 40,
    flexDirection: 'row',
    justifyContent: 'space-between',
    fontSize: 8,
    color: '#94a3b8',
  },
  chartImage: {
    width: '100%',
    height: 200,
    marginVertical: 10,
  },
});

interface PlayerReportData {
  player: {
    playerId: string;
    displayName?: string;
    riskScore: number;
    status: string;
    firstSeen: string;
    lastSeen: string;
  };
  flags: Array<{
    signalType: string;
    severity: string;
    confidence: number;
    createdAt: string;
    explanation: string;
  }>;
  connections: Array<{
    targetPlayerId: string;
    signalType: string;
    confidence: number;
  }>;
  events: Array<{
    actionType: string;
    timestamp: string;
    metadata: Record<string, any>;
  }>;
  timelineChart?: string; // Base64 image
  generatedAt: string;
  generatedBy: string;
}

export function PlayerReportDocument({ data }: { data: PlayerReportData }) {
  const getRiskBadgeStyle = (score: number) => {
    if (score >= 80) return styles.badgeRed;
    if (score >= 40) return styles.badgeYellow;
    return styles.badgeGreen;
  };

  return (
    <Document>
      <Page size="A4" style={styles.page}>
        {/* Header */}
        <View style={styles.header}>
          <View>
            <Text style={styles.title}>Player Investigation Report</Text>
            <Text style={styles.subtitle}>
              Generated: {new Date(data.generatedAt).toLocaleString()}
            </Text>
          </View>
          <View>
            <Image src="/logo.png" style={{ width: 100 }} />
          </View>
        </View>

        {/* Player Overview */}
        <View style={styles.section}>
          <Text style={styles.sectionTitle}>Player Overview</Text>
          <View style={styles.row}>
            <Text style={styles.label}>Player ID:</Text>
            <Text style={styles.value}>{data.player.playerId}</Text>
          </View>
          <View style={styles.row}>
            <Text style={styles.label}>Display Name:</Text>
            <Text style={styles.value}>{data.player.displayName || 'Unknown'}</Text>
          </View>
          <View style={styles.row}>
            <Text style={styles.label}>Risk Score:</Text>
            <View style={[styles.badge, getRiskBadgeStyle(data.player.riskScore)]}>
              <Text>{data.player.riskScore}/100</Text>
            </View>
          </View>
          <View style={styles.row}>
            <Text style={styles.label}>Status:</Text>
            <Text style={styles.value}>{data.player.status}</Text>
          </View>
          <View style={styles.row}>
            <Text style={styles.label}>First Seen:</Text>
            <Text style={styles.value}>
              {new Date(data.player.firstSeen).toLocaleString()}
            </Text>
          </View>
          <View style={styles.row}>
            <Text style={styles.label}>Last Seen:</Text>
            <Text style={styles.value}>
              {new Date(data.player.lastSeen).toLocaleString()}
            </Text>
          </View>
        </View>

        {/* Timeline Chart */}
        {data.timelineChart && (
          <View style={styles.section}>
            <Text style={styles.sectionTitle}>Activity Timeline</Text>
            <Image src={data.timelineChart} style={styles.chartImage} />
          </View>
        )}

        {/* Active Flags */}
        <View style={styles.section}>
          <Text style={styles.sectionTitle}>
            Flags ({data.flags.length})
          </Text>
          {data.flags.length > 0 ? (
            <View style={styles.table}>
              <View style={styles.tableHeader}>
                <Text style={[styles.tableHeaderCell, { width: '20%' }]}>Type</Text>
                <Text style={[styles.tableHeaderCell, { width: '15%' }]}>Severity</Text>
                <Text style={[styles.tableHeaderCell, { width: '15%' }]}>Confidence</Text>
                <Text style={[styles.tableHeaderCell, { width: '50%' }]}>Explanation</Text>
              </View>
              {data.flags.map((flag, idx) => (
                <View key={idx} style={styles.tableRow}>
                  <Text style={[styles.tableCell, { width: '20%' }]}>{flag.signalType}</Text>
                  <Text style={[styles.tableCell, { width: '15%' }]}>{flag.severity}</Text>
                  <Text style={[styles.tableCell, { width: '15%' }]}>
                    {(flag.confidence * 100).toFixed(0)}%
                  </Text>
                  <Text style={[styles.tableCell, { width: '50%' }]}>{flag.explanation}</Text>
                </View>
              ))}
            </View>
          ) : (
            <Text style={{ color: '#64748b' }}>No flags recorded.</Text>
          )}
        </View>

        {/* Linked Accounts */}
        <View style={styles.section}>
          <Text style={styles.sectionTitle}>
            Linked Accounts ({data.connections.length})
          </Text>
          {data.connections.length > 0 ? (
            <View style={styles.table}>
              <View style={styles.tableHeader}>
                <Text style={[styles.tableHeaderCell, { width: '40%' }]}>Player ID</Text>
                <Text style={[styles.tableHeaderCell, { width: '30%' }]}>Signal Type</Text>
                <Text style={[styles.tableHeaderCell, { width: '30%' }]}>Confidence</Text>
              </View>
              {data.connections.map((conn, idx) => (
                <View key={idx} style={styles.tableRow}>
                  <Text style={[styles.tableCell, { width: '40%' }]}>{conn.targetPlayerId}</Text>
                  <Text style={[styles.tableCell, { width: '30%' }]}>{conn.signalType}</Text>
                  <Text style={[styles.tableCell, { width: '30%' }]}>
                    {(conn.confidence * 100).toFixed(0)}%
                  </Text>
                </View>
              ))}
            </View>
          ) : (
            <Text style={{ color: '#64748b' }}>No linked accounts detected.</Text>
          )}
        </View>

        {/* Footer */}
        <View style={styles.footer} fixed>
          <Text>Behavior Analyzer - Confidential</Text>
          <Text>Generated by: {data.generatedBy}</Text>
          <Text render={({ pageNumber, totalPages }) => `${pageNumber} / ${totalPages}`} />
        </View>
      </Page>

      {/* Additional page for recent events */}
      {data.events.length > 0 && (
        <Page size="A4" style={styles.page}>
          <View style={styles.section}>
            <Text style={styles.sectionTitle}>
              Recent Events ({data.events.length})
            </Text>
            <View style={styles.table}>
              <View style={styles.tableHeader}>
                <Text style={[styles.tableHeaderCell, { width: '25%' }]}>Action</Text>
                <Text style={[styles.tableHeaderCell, { width: '25%' }]}>Timestamp</Text>
                <Text style={[styles.tableHeaderCell, { width: '50%' }]}>Details</Text>
              </View>
              {data.events.slice(0, 50).map((evt, idx) => (
                <View key={idx} style={styles.tableRow}>
                  <Text style={[styles.tableCell, { width: '25%' }]}>{evt.actionType}</Text>
                  <Text style={[styles.tableCell, { width: '25%' }]}>
                    {new Date(evt.timestamp).toLocaleString()}
                  </Text>
                  <Text style={[styles.tableCell, { width: '50%' }]}>
                    {JSON.stringify(evt.metadata).slice(0, 100)}
                  </Text>
                </View>
              ))}
            </View>
          </View>

          <View style={styles.footer} fixed>
            <Text>Behavior Analyzer - Confidential</Text>
            <Text>Generated by: {data.generatedBy}</Text>
            <Text render={({ pageNumber, totalPages }) => `${pageNumber} / ${totalPages}`} />
          </View>
        </Page>
      )}
    </Document>
  );
}
```

### 5.5 Export Button Component

```typescript
// src/components/reports/ExportButton.tsx
import { useState } from 'react';
import { Button, Menu, MenuItem, Loader } from '@aws-amplify/ui-react';
import { Download, FileText, FileSpreadsheet } from 'lucide-react';
import { pdf } from '@react-pdf/renderer';
import { PlayerReportDocument } from './PlayerReportDocument';
import { exportToCSV, exportToJSON } from '../../utils/export';

interface ExportButtonProps {
  playerId: string;
  playerData: any;
  flags: any[];
  connections: any[];
  events: any[];
  timelineChartRef?: React.RefObject<HTMLDivElement>;
}

export function ExportButton({
  playerId,
  playerData,
  flags,
  connections,
  events,
  timelineChartRef,
}: ExportButtonProps) {
  const [isGenerating, setIsGenerating] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);

  const generatePDF = async () => {
    setIsGenerating(true);
    try {
      // Capture chart as image if available
      let timelineChart: string | undefined;
      if (timelineChartRef?.current) {
        const canvas = await html2canvas(timelineChartRef.current);
        timelineChart = canvas.toDataURL('image/png');
      }

      const reportData = {
        player: {
          playerId,
          displayName: playerData?.displayName,
          riskScore: playerData?.riskScore || 0,
          status: playerData?.status || 'UNKNOWN',
          firstSeen: playerData?.firstSeen,
          lastSeen: playerData?.lastSeen,
        },
        flags,
        connections,
        events,
        timelineChart,
        generatedAt: new Date().toISOString(),
        generatedBy: 'Current User', // Get from auth context
      };

      const blob = await pdf(<PlayerReportDocument data={reportData} />).toBlob();

      // Download
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = `player-report-${playerId}-${Date.now()}.pdf`;
      link.click();
      URL.revokeObjectURL(url);
    } catch (error) {
      console.error('PDF generation failed:', error);
    } finally {
      setIsGenerating(false);
    }
  };

  const handleExportCSV = () => {
    exportToCSV(events, `events-${playerId}`);
    setMenuOpen(false);
  };

  const handleExportJSON = () => {
    exportToJSON({ playerData, flags, connections, events }, `player-${playerId}`);
    setMenuOpen(false);
  };

  return (
    <Menu
      isOpen={menuOpen}
      onOpenChange={setMenuOpen}
      trigger={
        <Button variation="primary" size="small">
          {isGenerating ? <Loader size="small" /> : <Download size={16} />}
          Export
        </Button>
      }
    >
      <MenuItem onClick={generatePDF}>
        <FileText size={16} />
        Export as PDF
      </MenuItem>
      <MenuItem onClick={handleExportCSV}>
        <FileSpreadsheet size={16} />
        Export Events as CSV
      </MenuItem>
      <MenuItem onClick={handleExportJSON}>
        <FileText size={16} />
        Export All as JSON
      </MenuItem>
    </Menu>
  );
}
```

### 5.6 Export Utilities

```typescript
// src/utils/export.ts
export function exportToCSV(data: any[], filename: string) {
  if (data.length === 0) return;

  const headers = Object.keys(data[0]);
  const csvContent = [
    headers.join(','),
    ...data.map(row =>
      headers.map(header => {
        const value = row[header];
        if (typeof value === 'object') {
          return `"${JSON.stringify(value).replace(/"/g, '""')}"`;
        }
        if (typeof value === 'string' && value.includes(',')) {
          return `"${value.replace(/"/g, '""')}"`;
        }
        return value;
      }).join(',')
    ),
  ].join('\n');

  downloadFile(csvContent, `${filename}.csv`, 'text/csv');
}

export function exportToJSON(data: any, filename: string) {
  const jsonContent = JSON.stringify(data, null, 2);
  downloadFile(jsonContent, `${filename}.json`, 'application/json');
}

function downloadFile(content: string, filename: string, mimeType: string) {
  const blob = new Blob([content], { type: mimeType });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
}
```

### 5.7 Integration in Player Profile

```typescript
// In PlayerProfile.tsx, add to header
<Flex gap="1rem">
  <Badge size="large" variation={...}>Risk Score: {riskScore}</Badge>
  <Badge size="large" variation="info">Status: {status}</Badge>
  <ExportButton
    playerId={id}
    playerData={profile}
    flags={flags}
    connections={links}
    events={events}
    timelineChartRef={timelineRef}
  />
</Flex>
```

---

## 6. Implementation Priority

| Feature | Priority | Effort | Dependencies |
|---------|----------|--------|--------------|
| Dark Mode Theme | High | Low | None |
| Export/Reports (PDF) | High | Medium | None |
| OAuth in Rust Plugin | Medium | Medium | Cognito M2M setup |
| WAF/CloudFront | Medium | Medium | AWS CDK knowledge |
| OpenSearch Integration | Low | High | Index design, Lambda |

### Recommended Order

1. **Dark Mode Theme** - Quick win, improves UX immediately
2. **Export/Reports** - Commonly requested feature, self-contained
3. **OAuth for Plugin** - Security improvement for production
4. **WAF/CloudFront** - Required for production deployment
5. **OpenSearch** - Complex feature, implement when forensic search becomes limiting

---

## 7. Testing Strategy

### 7.1 Dark Mode
- Visual regression tests with Chromatic/Percy
- Manual testing across all pages
- System preference detection tests

### 7.2 Export
- PDF generation unit tests
- CSV/JSON format validation
- Large dataset performance tests

### 7.3 OAuth
- Token refresh cycle tests
- Expiry handling tests
- Fallback to API key tests
- Integration tests with mock Cognito

### 7.4 WAF
- Rate limit validation
- Blocked request handling
- Geo-restriction tests
- Performance impact measurement

### 7.5 OpenSearch
- Index mapping validation
- Query result accuracy
- Aggregation tests
- Latency benchmarks
