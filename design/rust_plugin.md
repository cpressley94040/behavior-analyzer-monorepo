# Rust Behavior Telemetry Plugin (uMod)

**Design Document**

## 1. Purpose & Scope

### Purpose

The Rust uMod plugin captures **authoritative, server-side player behavior events** and forwards them to an external **Behavior Analysis Web App** for anomaly detection, forensic analysis, and account-linking. The plugin also offeres commands to admins to identify potential cheaters and to view forensic data in-game to help identify cheaters and collaborators.

### Non-Goals

* No cheat detection logic
* No punitive actions (kick/ban)
* No client-side trust
* No persistent analytics storage on the game server

## 2. Design Principles

1. **Server-Authoritative**

   * Only server-observed events are sent

2. **Low Overhead**

   * Zero allocations in hot paths where possible

3. **Fail-Safe**

   * Plugin failure never impacts gameplay

4. **Privacy-Aware**

   * Hashing of IP / HW identifiers

5. **Schema-Stable**

   * Forward-compatible event versions

## 3. Architecture Overview

```
+-------------------+
| Rust Server       |
| (Facepunch)      |
+-------------------+
        |
        v
+-------------------+
| uMod Plugin       |
|                   |
| - Event Capture   |
| - Normalization   |
| - Batching        |
| - Retry Queue     |
+-------------------+
        |
        v
+-------------------+
| HTTPS Endpoint    |
| (API Gateway)     |
+-------------------+
```

## 4. Plugin Responsibilities

| Responsibility | Description                           |
| -------------- | ------------------------------------- |
| Event capture  | Hook into Rust gameplay events        |
| Normalization  | Convert game events → platform schema |
| Batching       | Reduce HTTP overhead                  |
| Backoff        | Handle transient network failures     |
| Configuration  | Enable/disable signals                |
| Forensics      | View forensic data in-game            |

## 5. uMod Hook Integration

### Primary Hooks Used

| Hook | Purpose | Notes |
| :--- | :--- | :--- |
| `OnUserConnected` | Session start | Generates a unique `sessionId` for identity linking. |
| `OnUserDisconnected` | Session end | Clears the session and sends final metrics. |
| `OnPlayerAttack` | Combat engagement | Tracked for weapon accuracy, engagement patterns, and wounding. |
| `OnMeleeAttack` | Melee combat | Tracked for close-quarters engagement patterns. |
| `OnPlayerDeath` | Death events | Used to calculate K/D and identify streaks. |
| `OnPlayerViolation` | Anti-hack violation | Captures server-side anti-hack trigger metadata. |
| `OnLootPlayer` | Loot player interactions | Tracked for suspect looting behavior (e.g. speed-looting). |
| `OnLootEntity` | Loot entity interactions | Tracked for stash/box looting patterns. |
| `OnStashHidden` | Stash hidden | Monitors stash placement behavior. |
| `OnStashExposed` | Stash exposed | Monitors stash finding behavior (ESP detection). |
| `OnPlayerReported` | Player reported | Integrates in-game reports as behavioral signals. |
| `OnClanCreated` | Clan growth | Monitors group formation. |
| `OnClanMemberAdded` | Clan association | key for identifying team-based behavior. |
| `OnClanMemberKicked` | Group conflict | Captured for risk profiling. |
| `OnUserGroupAdded` | Permission change | Tracks admin/vip status changes. |
| `OnPlayerChat` | Communication | Sentiment analysis and toxicity detection. |

## 6. Event Model

### 6.1 Internal Event Structure

```csharp
class TelemetryEvent
{
    public string eventId { get; set; }
    public string playerId { get; set; }
    public string accountId { get; set; }
    public string sessionId { get; set; }
    public string actionType { get; set; }
    public long timestamp { get; set; }
    public Dictionary<string, object> metadata { get; set; }
    public ClientFingerprint fingerprint { get; set; }
    public int version { get; set; }
    
    public TelemetryEvent()
    {
        metadata = new Dictionary<string, object>();
        version = 1;
    }
    
    // Reset method for object pooling
    public TelemetryEvent Reset(BasePlayer player, string actionType)
    {
        eventId = Guid.NewGuid().ToString();
        playerId = player.UserIDString;
        accountId = player.UserIDString;
        sessionId = GetSessionId(player);
        this.actionType = actionType;
        timestamp = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
        metadata.Clear();
        fingerprint = CreateFingerprint(player);
        version = 1;
        return this;
    }
}
```

### 6.2 Client Fingerprint

The client fingerprint is a hash of the client's IP address and language. This information will be supplemented with additional data from steamdb.info based on the player's Steam ID.

```csharp
class ClientFingerprint
{
    public string ipHash { get; set; }
    public string language { get; set; }
    
    public ClientFingerprint() { }
    
    public ClientFingerprint(string ipHash, string lang)
    {
        ip_hash = ipHash;
        language = lang;
    }
}
```

**Notes**

* `ip_hash = SHA256(IP + server_salt)`

### 6.3 OAuth Response Model

```csharp
class OAuthResponse
{
    public string access_token { get; set; }
    public string token_type { get; set; }
    public int expires_in { get; set; }
}
```

### 6.4 API Response Models

```csharp
class PlayerRiskData
{
    public string player_id { get; set; }
    public float risk_score { get; set; }
    public int flags_open { get; set; }
    public long last_seen { get; set; }
    public FlagData[] recent_flags { get; set; }
}

class FlagData
{
    public string signal { get; set; }
    public string severity { get; set; }
    public string explanation { get; set; }
}

class LinkedAccountsData
{
    public string player_id { get; set; }
    public LinkedAccount[] links { get; set; }
}

class LinkedAccount
{
    public string player_id { get; set; }
    public string player_name { get; set; }
    public float confidence { get; set; }
    public string signal_type { get; set; }
}
```

## 7. Event Types (Initial Set)

### Combat

* `PLAYER_HIT`
* `PLAYER_KILLED`

### Inventory

* `ITEM_LOOTED`

### Session

* `SESSION_START`
* `SESSION_END`

## 8. Event Normalization Example

### Raw Event

**OnPlayerAttack**

* Useful for modifying an attack before it goes out
* hitInfo.HitEntity should be the entity that this attack would hit
* Returning a non-null value overrides default behavior

```csharp
object OnPlayerAttack(BasePlayer attacker, HitInfo info)
{
    Puts("OnPlayerAttack works!");
    return null;
}
```

### Normalized Event

```json
{
  "event_id": "uuid",
  "player_id": "7656119...",
  "session_id": "sess_abc",
  "action_type": "PLAYER_HIT",
  "timestamp": 1734567890123,
  "metadata": {
    "weapon": "rifle.ak",
    "hit_bone": "head",
    "distance": 134.2,
    "target_player_id": "7656119..."
  },
  "version": 1
}
```

## 9. Event Batching & Transport

### 9.1 Batching Strategy

| Parameter      | Value      |
| -------------- | ---------- |
| Max batch size | 50 events  |
| Max batch age  | 2 seconds  |
| Transport      | HTTPS POST |
| Compression    | GZIP       |

### 9.2 Retry Queue

* In-memory ring buffer
* Max size configurable
* Exponential backoff
* Drop oldest on overflow

```text
Send → Fail → Retry (2s → 5s → 15s → Drop)
```

## 10. Configuration

Plugin configuration files are in the umod/config directory. By default, a plugin configuration file is named after the plugin that created them with a .json file extension.

### Configuration Schematic

Define a class schematic to implement a serializable contract that represents the shape of the configuration file.

```csharp
namespace uMod.Plugins
{
    [Info("BehaviorAnalyzer", "Corie", "0.1.0")]
    [Description("Detects anomalous action sequences and similarity to known bad actors")]
    public class BehaviorAnalyzer : RustPlugin
    {
        [Config]
        class DefaultConfig
        {
            // API Configuration
            public string Endpoint = "https://api.example.com/ingest";
            public string ApiKey = "redacted";
            
            // OAuth Configuration
            public string OAuthClientId = "";
            public string OAuthClientSecret = "";
            public string OAuthTokenUrl = "";
            
            // Event Configuration
            public string[] EnabledEvents = ["PLAYER_HIT", "PLAYER_KILLED", "WEAPON_FIRED"];
            
            // Batching Configuration
            public int BatchSize = 50;
            public int FlushIntervalMs = 2000;
            public int MaxRetries = 3;
            public int RetryDelayMs = 5000;
            
            // Performance Configuration
            public bool UsePooling = true;
            public int MaxQueueSize = 1000;
            
            // Privacy Configuration
            public string ServerSalt = "";
            public bool HashIpAddresses = true;
        }
        
        private DefaultConfig config;
        
        void Loaded(DefaultConfig defaultConfig)
        {
            config = defaultConfig;
        }
    }
}
```
### JSON Config

```json
{
  "endpoint": "https://api.example.com/ingest",
  "api_key": "redacted",
  "oauth_client_id": "",
  "oauth_client_secret": "",
  "oauth_token_url": "",
  "enabled_events": [
    "PLAYER_HIT",
    "PLAYER_KILLED",
    "WEAPON_FIRED"
  ],
  "batch_size": 50,
  "flush_interval_ms": 2000,
  "max_retries": 3,
  "retry_delay_ms": 5000,
  "use_pooling": true,
  "max_queue_size": 1000,
  "server_salt": "",
  "hash_ip_addresses": true
}
```

## 11. Performance Considerations

### 11.1 Object Pooling

Use uMod pooling to reduce memory allocations and garbage collection pressure:

```csharp
// Pool event objects
private uMod.Pooling.DynamicPool<TelemetryEvent> eventPool;

void Init()
{
    eventPool = uMod.Pooling.Pools.Default<TelemetryEvent>();
}

void OnPlayerAttack(BasePlayer attacker, HitInfo info)
{
    TelemetryEvent evt = eventPool.Get();
    try
    {
        evt.Reset(attacker, info);
        EnqueueEvent(evt);
    }
    finally
    {
        eventPool.Free(evt);
    }
}
```

### 11.2 Array Pooling

Use array pools for temporary buffers:

```csharp
// Get pooled array for batch processing
object[] batch = uMod.Pooling.ArrayPool.Get(config.BatchSize);
try
{
    // Process batch
}
finally
{
    uMod.Pooling.ArrayPool.Free(batch);
}
```

### 11.3 List Pooling

Pool frequently used collections:

```csharp
List<TelemetryEvent> events = uMod.Pooling.Pools.GetList<TelemetryEvent>();
try
{
    // Add events
    events.Add(evt1);
    events.Add(evt2);
    // Process
}
finally
{
    uMod.Pooling.Pools.FreeList(ref events);
}
```

### 11.4 Performance Guidelines

* Avoid LINQ in hot paths (OnPlayerAttack, OnFrame, etc.)
* Use pooling for all frequently allocated objects
* Async HTTP off main thread via Web client
* Metadata truncation limits (max 1KB per field)
* Use StringBuilder pool for string concatenation

## 12. Web Request Implementation

### 12.1 OAuth Authentication

```csharp
private string accessToken;
private DateTime tokenExpiry;

void OnServerInitialized()
{
    // Authenticate with OAuth on startup
    AuthenticateWithOAuth();
}

void AuthenticateWithOAuth()
{
    if (string.IsNullOrEmpty(config.OAuthClientId))
    {
        Puts("OAuth not configured, using API key");
        return;
    }
    
    var formData = new Dictionary<string, string>
    {
        { "grant_type", "client_credentials" },
        { "client_id", config.OAuthClientId },
        { "client_secret", config.OAuthClientSecret }
    };
    
    Web.Post(config.OAuthTokenUrl, null, formData)
        .Done(response =>
        {
            if (response.StatusCode == 200)
            {
                var data = JsonConvert.DeserializeObject<OAuthResponse>(response.ReadAsString());
                accessToken = data.access_token;
                tokenExpiry = DateTime.UtcNow.AddSeconds(data.expires_in - 60);
                Puts("OAuth authentication successful");
            }
        })
        .Fail(response =>
        {
            PrintError($"OAuth failed: {response.StatusCode} {response.StatusDescription}");
        });
}
```

### 12.2 Batched Event Transmission

```csharp
private Queue<TelemetryEvent> eventQueue = new Queue<TelemetryEvent>();
private Timer flushTimer;

void Init()
{
    flushTimer = timer.Every(config.FlushIntervalMs / 1000f, FlushEvents);
}

void EnqueueEvent(TelemetryEvent evt)
{
    if (eventQueue.Count >= config.MaxQueueSize)
    {
        // Drop oldest event
        eventQueue.Dequeue();
        PrintWarning("Event queue overflow, dropping oldest event");
    }
    
    eventQueue.Enqueue(evt);
    
    // Flush immediately if batch is full
    if (eventQueue.Count >= config.BatchSize)
    {
        FlushEvents();
    }
}

void FlushEvents()
{
    if (eventQueue.Count == 0) return;
    
    // Check if OAuth token needs refresh
    if (!string.IsNullOrEmpty(config.OAuthClientId) && 
        DateTime.UtcNow >= tokenExpiry)
    {
        AuthenticateWithOAuth();
        return; // Will flush after auth completes
    }
    
    List<TelemetryEvent> batch = uMod.Pooling.Pools.GetList<TelemetryEvent>();
    try
    {
        int count = Math.Min(eventQueue.Count, config.BatchSize);
        for (int i = 0; i < count; i++)
        {
            batch.Add(eventQueue.Dequeue());
        }
        
        SendBatch(batch);
    }
    finally
    {
        uMod.Pooling.Pools.FreeList(ref batch);
    }
}
```

### 12.3 HTTP Request with Retry

```csharp
void SendBatch(List<TelemetryEvent> events, int retryCount = 0)
{
    string json = JsonConvert.SerializeObject(events);
    
    var headers = new Dictionary<string, string>
    {
        { "Content-Type", "application/json" }
    };
    
    // Add authentication
    if (!string.IsNullOrEmpty(accessToken))
    {
        headers["Authorization"] = $"Bearer {accessToken}";
    }
    else if (!string.IsNullOrEmpty(config.ApiKey))
    {
        headers["X-API-Key"] = config.ApiKey;
    }
    
    Web.Post(config.Endpoint, headers, json)
        .Done(response =>
        {
            if (response.StatusCode == 200)
            {
                Puts($"Successfully sent {events.Count} events");
            }
            else
            {
                PrintWarning($"Server returned {response.StatusCode}: {response.ReadAsString()}");
            }
        })
        .Fail(response =>
        {
            if (retryCount < config.MaxRetries)
            {
                float delay = config.RetryDelayMs / 1000f * (float)Math.Pow(2, retryCount);
                PrintWarning($"Request failed, retrying in {delay}s (attempt {retryCount + 1}/{config.MaxRetries})");
                timer.Once(delay, () => SendBatch(events, retryCount + 1));
            }
            else
            {
                PrintError($"Failed to send batch after {config.MaxRetries} retries");
            }
        });
}
```

## 13. Failure Modes & Handling

| Failure             | Behavior       |
| ------------------- | -------------- |
| Endpoint down       | Buffer + drop  |
| Serialization error | Skip event     |
| Config error        | Disable plugin |
| Plugin exception    | Catch & log    |

**Never**

* Block main thread
* Crash server
* Modify gameplay

## 14. Admin Commands

### 14.1 Command Structure

Admin commands provide in-game access to forensic data and analysis results.

```csharp
// Register permissions in Init
void Init()
{
    permission.RegisterPermission("behavioranalyzer.admin", this);
    permission.RegisterPermission("behavioranalyzer.moderator", this);
}
```

### 14.2 Check Player Command

Query the web app for a player's risk score and recent flags:

```csharp
[Command("ba.check"), Permission("behavioranalyzer.moderator")]
void CheckPlayerCommand(IPlayer player, string command, string[] args)
{
    if (args.Length == 0)
    {
        player.Reply("Usage: ba.check <player>");
        return;
    }
    
    IPlayer target = players.FindPlayer(args[0]);
    if (target == null)
    {
        player.Reply($"Player '{args[0]}' not found");
        return;
    }
    
    // Query web app API
    var headers = new Dictionary<string, string>
    {
        { "Authorization", $"Bearer {accessToken}" }
    };
    
    Web.Get($"{config.Endpoint}/players/{target.Id}/risk", headers)
        .Done(response =>
        {
            if (response.StatusCode == 200)
            {
                var data = JsonConvert.DeserializeObject<PlayerRiskData>(response.ReadAsString());
                player.Reply($"Player: {target.Name}");
                player.Reply($"Risk Score: {data.risk_score:F2}");
                player.Reply($"Open Flags: {data.flags_open}");
                player.Reply($"Last Seen: {FormatTimestamp(data.last_seen)}");
                
                if (data.recent_flags?.Length > 0)
                {
                    player.Reply("\
Recent Flags:");
                    foreach (var flag in data.recent_flags)
                    {
                        player.Reply($"  [{flag.severity}] {flag.signal}: {flag.explanation}");
                    }
                }
            }
        })
        .Fail(response =>
        {
            player.Reply("Failed to retrieve player data");
        });
}
```

### 14.3 Link Detection Command

Find potentially linked accounts:

```csharp
[Command("ba.links"), Permission("behavioranalyzer.admin")]
void CheckLinksCommand(IPlayer player, string command, string[] args)
{
    if (args.Length == 0)
    {
        player.Reply("Usage: ba.links <player>");
        return;
    }
    
    IPlayer target = players.FindPlayer(args[0]);
    if (target == null)
    {
        player.Reply($"Player '{args[0]}' not found");
        return;
    }
    
    var headers = new Dictionary<string, string>
    {
        { "Authorization", $"Bearer {accessToken}" }
    };
    
    Web.Get($"{config.Endpoint}/players/{target.Id}/links", headers)
        .Done(response =>
        {
            if (response.StatusCode == 200)
            {
                var data = JsonConvert.DeserializeObject<LinkedAccountsData>(response.ReadAsString());
                player.Reply($"Linked Accounts for {target.Name}:");
                
                if (data.links?.Length > 0)
                {
                    foreach (var link in data.links)
                    {
                        player.Reply($"  {link.player_name} (Confidence: {link.confidence:P0}, Signal: {link.signal_type})");
                    }
                }
                else
                {
                    player.Reply("  No linked accounts detected");
                }
            }
        })
        .Fail(response =>
        {
            player.Reply("Failed to retrieve link data");
        });
}
```

### 14.4 Stats Command

Show plugin statistics:

```csharp
[Command("ba.stats"), Permission("behavioranalyzer.moderator")]
void StatsCommand(IPlayer player, string command, string[] args)
{
    player.Reply("=== Behavior Analyzer Stats ===");
    player.Reply($"Events in Queue: {eventQueue.Count} / {config.MaxQueueSize}");
    player.Reply($"Batch Size: {config.BatchSize}");
    player.Reply($"Flush Interval: {config.FlushIntervalMs}ms");
    player.Reply($"Enabled Events: {string.Join(", ", config.EnabledEvents)}");
    player.Reply($"OAuth Active: {!string.IsNullOrEmpty(accessToken)}");
    
    if (!string.IsNullOrEmpty(accessToken))
    {
        var timeUntilExpiry = tokenExpiry - DateTime.UtcNow;
        player.Reply($"Token Expires In: {timeUntilExpiry.Minutes}m {timeUntilExpiry.Seconds}s");
    }
}
```

## 15. Security Model

* API key authentication
* HMAC signature (optional)
* TLS enforced
* Replay protection via nonce

## 14. Privacy & Compliance

* No plaintext IPs
* No chat logs
* No PII
* Configurable redaction

## 15. UML – Sequence Diagram

```
Player
  |
  | Attack
  v
Rust Server
  |
  | OnPlayerAttack
  v
uMod Plugin
  |
  | Normalize Event
  | Add to Batch
  v
Batch Timer
  |
  | POST /ingest
  v
Web App
```

## 16. Extensibility

* New hooks → new action types
* Metadata keys are additive
* Versioned schema
* Feature flags per server

## 17. Testing Strategy

### 17.1 Unit Tests

#### Hashing Tests

```csharp
[Test]
public void TestIPHashing()
{
    string ip = "192.168.1.1";
    string salt = "test_salt";
    string hash1 = HashIP(ip, salt);
    string hash2 = HashIP(ip, salt);
    
    // Same input should produce same hash
    Assert.AreEqual(hash1, hash2);
    
    // Hash should be SHA256 length
    Assert.AreEqual(64, hash1.Length);
    
    // Different salt should produce different hash
    string hash3 = HashIP(ip, "different_salt");
    Assert.AreNotEqual(hash1, hash3);
}
```

#### Serialization Tests

```csharp
[Test]
public void TestEventSerialization()
{
    var evt = new TelemetryEvent
    {
        event_id = "test-123",
        player_id = "76561198000000000",
        action_type = "PLAYER_HIT",
        timestamp = 1734567890123,
        metadata = new Dictionary<string, object>
        {
            { "weapon", "rifle.ak" },
            { "distance", 134.2 }
        }
    };
    
    string json = JsonConvert.SerializeObject(evt);
    var deserialized = JsonConvert.DeserializeObject<TelemetryEvent>(json);
    
    Assert.AreEqual(evt.event_id, deserialized.event_id);
    Assert.AreEqual(evt.player_id, deserialized.player_id);
    Assert.AreEqual(evt.action_type, deserialized.action_type);
}
```

#### Batch Logic Tests

```csharp
[Test]
public void TestBatchFlushing()
{
    var queue = new Queue<TelemetryEvent>();
    int batchSize = 50;
    
    // Add 75 events
    for (int i = 0; i < 75; i++)
    {
        queue.Enqueue(CreateMockEvent());
    }
    
    // Should flush 50 events
    var batch = new List<TelemetryEvent>();
    int count = Math.Min(queue.Count, batchSize);
    for (int i = 0; i < count; i++)
    {
        batch.Add(queue.Dequeue());
    }
    
    Assert.AreEqual(50, batch.Count);
    Assert.AreEqual(25, queue.Count);
}
```

### 17.2 Integration Tests

#### Mock HTTPS Endpoint

```csharp
// Use a test HTTP server to validate request format
[Test]
public void TestEventSubmission()
{
    using (var server = new MockHttpServer(8080))
    {
        server.OnPost("/ingest", (req, res) =>
        {
            // Validate headers
            Assert.IsTrue(req.Headers.ContainsKey("Authorization"));
            Assert.AreEqual("application/json", req.Headers["Content-Type"]);
            
            // Validate body
            var events = JsonConvert.DeserializeObject<List<TelemetryEvent>>(req.Body);
            Assert.IsNotNull(events);
            Assert.IsTrue(events.Count > 0);
            
            res.StatusCode = 200;
            res.Body = "{"status":"success"}";
        });
        
        // Run plugin test
        plugin.Config.Endpoint = "http://localhost:8080/ingest";
        plugin.OnPlayerAttack(mockPlayer, mockHitInfo);
        plugin.FlushEvents();
        
        // Wait for async request
        Thread.Sleep(1000);
        
        Assert.AreEqual(1, server.RequestCount);
    }
}
```

#### Load Test with Bots

```csharp
[Test]
public void TestHighVolumeEvents()
{
    int eventCount = 10000;
    var stopwatch = Stopwatch.StartNew();
    
    for (int i = 0; i < eventCount; i++)
    {
        plugin.OnPlayerAttack(CreateMockPlayer(), CreateMockHitInfo());
    }
    
    stopwatch.Stop();
    
    // Should process 10k events in under 1 second
    Assert.IsTrue(stopwatch.ElapsedMilliseconds < 1000);
    
    // Queue should not overflow
    Assert.IsTrue(plugin.EventQueueCount <= plugin.Config.MaxQueueSize);
}
```

### 17.3 Validation

#### Event Schema Compliance

```csharp
[Test]
public void TestEventSchemaCompliance()
{
    var evt = CreatePlayerHitEvent();
    
    // Required fields
    Assert.IsNotNull(evt.event_id);
    Assert.IsNotNull(evt.player_id);
    Assert.IsNotNull(evt.action_type);
    Assert.IsTrue(evt.timestamp > 0);
    
    // Field formats
    Assert.IsTrue(Guid.TryParse(evt.event_id, out _));
    Assert.IsTrue(evt.player_id.StartsWith("7656119")); // SteamID64 format
    
    // Metadata size limits
    string metadataJson = JsonConvert.SerializeObject(evt.metadata);
    Assert.IsTrue(metadataJson.Length < 10240); // 10KB limit
}
```

#### Latency Monitoring

```csharp
void OnEventSent(TelemetryEvent evt, TimeSpan duration)
{
    // Log slow requests
    if (duration.TotalMilliseconds > 1000)
    {
        PrintWarning($"Slow event transmission: {duration.TotalMilliseconds}ms");
    }
    
    // Track average latency
    averageLatency = (averageLatency * eventsSent + duration.TotalMilliseconds) / (eventsSent + 1);
    eventsSent++;
}
```

## 18. Deployment

1. Drop `.cs` plugin into uMod
2. Configure endpoint + API key
3. Reload plugin
4. Verify heartbeat event

## 19. Risks & Mitigations

| Risk             | Mitigation        |
| ---------------- | ----------------- |
| Network lag      | Async batching    |
| Cheater spoofing | Server-only hooks |
| Schema drift     | Versioning        |
| Plugin overhead  | Sampling controls |

## 20. Summary

This plugin:

* Captures authoritative gameplay signals
* Is performance-safe
* Is privacy-aware
* Feeds a scalable behavior intelligence system

It intentionally **does less**, so the platform can **do more**.
