# Extended Client Fingerprinting for Ban Evasion Detection

## Problem Statement

The current `ClientFingerprint` captures only two signals: a SHA-256 hash of the player's IP address and a two-letter language code. This is insufficient for reliable ban evasion detection because:

- **IP is trivially spoofed.** VPNs, proxies, and dynamic ISP assignments make IP the weakest identity signal.
- **Language is low-entropy.** Most players on a given server share the same language, making it nearly useless as a distinguishing feature.
- **The IdentityDetector's `LinkType::DEVICE` path has no data source.** The backend defines a DEVICE link type (35% of the hw/ip signal weight), but nothing in the plugin populates it.

The Rust dedicated server binary (`Assembly-CSharp.dll` v2.0.6828) exposes significantly richer client-reported data through `Network.Connection` and `UnityEngine.SystemInfo` that is currently ignored.

## Goals

1. Capture all server-accessible hardware and environment signals from the Rust client.
2. Produce a composite device fingerprint hash that is resistant to single-field spoofing.
3. Feed DEVICE-type links into the IdentityDetector graph alongside existing IP links.
4. Preserve privacy controls (hashing, salt, opt-out) consistent with existing `HashIpAddresses` behavior.
5. Maintain backward compatibility — old events with the two-field fingerprint continue to work.

## Non-Goals

- Client-side anti-cheat or kernel-level fingerprinting (out of scope for a server-side Oxide plugin).
- Bypassing EAC or Steam authentication (those are separate systems).
- Fingerprinting via packet timing or network analysis.

## Available Server-Side APIs

Extracted from the Rust dedicated server assemblies in `third_party/RustDedicated_Data/Managed/`:

### UnityEngine.SystemInfo (client-reported at connect time)

| Property | API | Entropy | Spoofability |
|----------|-----|---------|-------------|
| Device unique ID | `SystemInfo.deviceUniqueIdentifier` | High | Medium (registry editable) |
| Device name | `SystemInfo.deviceName` | Medium | Easy (hostname) |
| GPU name | `SystemInfo.graphicsDeviceName` | Medium | Hard (driver-level) |
| GPU vendor | `SystemInfo.graphicsDeviceVendor` | Low | Hard |
| GPU driver version | `SystemInfo.graphicsDeviceVersion` | Medium | Medium |
| VRAM size (MB) | `SystemInfo.graphicsMemorySize` | Medium | Hard |
| Shader level | `SystemInfo.graphicsShaderLevel` | Low | Hard |
| System memory (MB) | `SystemInfo.systemMemorySize` | Medium | Hard |
| Processor type | `SystemInfo.processorType` | Medium | Hard |
| Processor count | `SystemInfo.processorCount` | Low | Hard |
| OS family | `SystemInfo.operatingSystemFamily` | Low | Hard |

### Network.Connection (available at auth time)

| Property | API | Entropy | Spoofability |
|----------|-----|---------|-------------|
| IP address | `connection.ipaddress` | High | Easy (VPN) |
| Protocol version | `connection.protocol` | Low | Hard |
| Auth level | `connection.authLevel` | Low | N/A |
| EAC token | `connection.anticheatToken` | Per-session | N/A |

### OnPlayerSetInfo Hook (client console variables)

| Key | Example Value | Entropy |
|-----|---------------|---------|
| `"global.language"` | `"en"` | Low |
| `"global.streamermode"` | `"True"` | Low |
| Custom convars | Varies | Varies |

**Key constraint:** `SystemInfo` properties reflect the *server's* hardware when read server-side. To get *client* hardware info, the server must read it from the client's connection payload. The `Connection.ClientInfo` field and the `OnPlayerSetInfo` hook are the two surfaces where client-reported system data arrives. The exact fields available in `ClientInfo` depend on the Rust client build version.

## Design

### Extended ClientFingerprint Structure

```csharp
private class ClientFingerprint
{
    // --- Existing fields (backward compatible) ---
    public string ipHash { get; set; }
    public string language { get; set; }

    // --- New fields ---
    public string deviceHash { get; set; }      // Composite hardware fingerprint hash
    public string gpuId { get; set; }            // "NVIDIA GeForce RTX 3080" (hashed if configured)
    public int vramMB { get; set; }              // 10240
    public int systemMemoryMB { get; set; }      // 32768
    public string processorId { get; set; }      // "Intel Core i9-12900K" (hashed if configured)
    public int processorCount { get; set; }      // 24
    public string osFamily { get; set; }         // "Windows", "Linux", "MacOSX"
    public uint protocol { get; set; }           // Network protocol version
    public int shaderLevel { get; set; }         // 50 (SM 5.0)

    // --- Composite fingerprint ---
    // deviceHash = SHA256(gpuId + vramMB + systemMemoryMB + processorId
    //                     + processorCount + osFamily + shaderLevel + ServerSalt)
    //
    // This hash is the primary DEVICE signal for the IdentityDetector.
    // Individual fields are stored for forensic drill-down and partial matching.
}
```

### Fingerprint Collection

```
OnPlayerConnected(BasePlayer player)
    │
    ├─ Read player.net.connection.ipaddress       → ipHash (existing)
    ├─ Read player.net.connection.protocol        → protocol (new)
    ├─ Read player.IPlayer.Language               → language (existing)
    │
    ├─ Attempt client hardware read:
    │   ├─ Read connection ClientInfo fields if available
    │   ├─ Fallback: buffer OnPlayerSetInfo key/value pairs
    │   │   for known hardware-reporting convars
    │   └─ Fallback: leave hardware fields null
    │
    └─ Compute deviceHash from available hardware fields
       (only non-null fields contribute to the hash)
```

#### Collection Strategy

The plugin cannot call `SystemInfo` directly (it returns server hardware). Client hardware data arrives through two possible channels:

1. **`Connection.ClientInfo`** — A binary payload sent during the authentication handshake. Its structure is internal to Rust and may vary across game updates. The plugin should attempt to read known offsets and gracefully degrade if the format changes.

2. **`OnPlayerSetInfo` buffering** — The Rust client sends console variable updates after connecting. Some convars contain system info. The plugin should buffer SetInfo calls during the first N seconds after connect, then finalize the fingerprint.

The recommended approach is **strategy 2** (OnPlayerSetInfo buffering) because:
- It uses a stable, documented Oxide hook.
- It doesn't depend on binary offsets in `ClientInfo` that break across Rust updates.
- It can be extended by server operators who install client-side mods that report additional convars.

For the `deviceUniqueIdentifier` and GPU/CPU data that clients don't send via convars, the plugin should attempt a direct read from `Connection` if the Rust version supports it, and leave the field null otherwise.

### Composite Hash Algorithm

```
deviceHash = SHA256(
    normalize(gpuId) + "|" +
    vramMB.ToString() + "|" +
    systemMemoryMB.ToString() + "|" +
    normalize(processorId) + "|" +
    processorCount.ToString() + "|" +
    osFamily + "|" +
    shaderLevel.ToString() + "|" +
    ServerSalt
)
```

Where `normalize()` lowercases and strips whitespace to prevent trivial variations from producing different hashes.

**Partial matching:** When some fields are unavailable (null), they are excluded from the hash. The backend must track which fields contributed to each hash so that two fingerprints with different field availability aren't falsely compared.

To support this, add a `fieldMask` bitmask:

```csharp
public int fieldMask { get; set; }  // Bitmask of which fields contributed to deviceHash
// Bit 0: gpuId
// Bit 1: vramMB
// Bit 2: systemMemoryMB
// Bit 3: processorId
// Bit 4: processorCount
// Bit 5: osFamily
// Bit 6: shaderLevel
```

Two fingerprints should only be compared when their `fieldMask` values share at least 3 set bits.

### Data Flow

```
Rust Plugin                    Frontend                      Backend
    │                              │                            │
    │  POST /batchIngest           │                            │
    │  { fingerprint: {            │                            │
    │      ipHash, deviceHash,     │                            │
    │      gpuId, vramMB, ...      │                            │
    │      fieldMask               │                            │
    │  }}                          │                            │
    │ ─────────────────────────> │                            │
    │                              │  DynamoDB                  │
    │                              │  TelemetryEvent.metadata   │
    │                              │  (fingerprint JSON)        │
    │                              │ ──────────────────────>  │
    │                              │                            │
    │                              │                            │  Extract fingerprint
    │                              │                            │  from event context
    │                              │                            │
    │                              │                            │  For each pair of players
    │                              │                            │  sharing a deviceHash:
    │                              │                            │    add_link({
    │                              │                            │      source, target,
    │                              │                            │      LinkType::DEVICE,
    │                              │                            │      confidence,
    │                              │                            │      timestamp
    │                              │                            │    })
    │                              │                            │
    │                              │  AccountLink table         │
    │                              │  signalType: "DEVICE"    │
    │                              │ <──────────────────────  │
```

### Backend Integration

The `IdentityDetector` already supports `LinkType::DEVICE` but currently receives no DEVICE links. The integration requires:

1. **Fingerprint extraction in the ingestion pipeline.** When processing a `TelemetryEvent`, extract `fingerprint.deviceHash` from the event context map and store it in a per-entity fingerprint index.

2. **Pairwise link creation.** Maintain an inverted index: `deviceHash → set<entity_id>`. When a new entity appears with a known deviceHash, create DEVICE links to all other entities sharing that hash. Confidence scales with `fieldMask` overlap:
   - 7/7 fields: confidence = 0.95
   - 5-6 fields: confidence = 0.80
   - 3-4 fields: confidence = 0.60
   - <3 fields: do not create link

3. **Existing IP link creation continues unchanged.** The same inverted-index pattern already applies to `ipHash`.

4. **Weight rebalancing.** With DEVICE data available, the signal weights should shift:

   | Signal | Current Weight | Proposed Weight |
   |--------|---------------|-----------------|
   | HW/IP reuse | 0.35 | 0.40 |
   | Skill ramp | 0.30 | 0.25 |
   | Behavior similarity | 0.20 | 0.20 |
   | Sequence similarity | 0.15 | 0.15 |

   The HW/IP weight increases slightly because it now carries two independent signals (IP and DEVICE) instead of one.

### GraphQL Schema Changes

Add fingerprint fields to the `AccountLink` model for forensic visibility:

```typescript
AccountLink: a.model({
    sourcePlayerId: a.string().required(),
    owner: a.string().required(),
    targetPlayerId: a.string().required(),
    signalType: a.string(),       // "IP", "DEVICE", "BEHAVIOR" (existing)
    confidence: a.float(),
    lastSeen: a.float(),
    deviceHash: a.string(),       // NEW: shared device hash (if signalType=DEVICE)
    fieldMask: a.integer(),       // NEW: which fields contributed
})
```

### Privacy Controls

Extend the plugin configuration:

```json
{
    "HashIpAddresses": true,
    "HashHardwareFields": true,
    "ServerSalt": "your-secret-salt",
    "FingerprintEnabled": true,
    "FingerprintCollectionWindowSeconds": 10
}
```

- `HashHardwareFields`: When true, individual fields (gpuId, processorId) are SHA-256 hashed before transmission. The composite `deviceHash` is always hashed.
- `FingerprintEnabled`: Master switch. When false, the plugin sends the legacy two-field fingerprint only.
- `FingerprintCollectionWindowSeconds`: How long to buffer `OnPlayerSetInfo` calls before finalizing the fingerprint.

### Test Stubs Update

The test `Connection` stub in `Stubs.cs` needs extension:

```csharp
public class Connection
{
    public ulong userid;
    public string ipaddress;
    public uint protocol;
    // New: simulated client info fields for fingerprint testing
    public string deviceName;
    public string graphicsDeviceName;
    public int graphicsMemorySize;
    public int systemMemorySize;
    public string processorType;
    public int processorCount;
}
```

## Rollout Plan

### Phase 1: Plugin fingerprint collection

- Extend `ClientFingerprint` class with new fields.
- Implement `OnPlayerSetInfo` buffering with configurable window.
- Attempt `Connection`-based hardware reads with graceful fallback.
- Compute `deviceHash` and `fieldMask`.
- Update test stubs and add unit tests for `CreateFingerprint`.
- Gate behind `FingerprintEnabled` config flag (default: `true`).

### Phase 2: Backend link creation

- Add fingerprint extraction to the ingestion pipeline event processing.
- Implement `deviceHash → entity_id` inverted index.
- Create `LinkType::DEVICE` links on hash collision.
- Add confidence scaling by `fieldMask` overlap.
- Add unit tests for DEVICE link creation and identity scoring.

### Phase 3: Frontend forensic visibility

- Add `deviceHash` and `fieldMask` columns to `AccountLink` table in GraphQL schema.
- Display shared hardware info in the network graph visualization (node tooltips).
- Add "Hardware Match" badge to account link edges in the graph view.

### Phase 4: Weight tuning

- Collect fingerprint data from production servers for 2-4 weeks.
- Measure DEVICE link precision (true alt-account rate) vs false positive rate.
- Tune `hw_ip_weight` and confidence thresholds based on observed data.
- Consider splitting HW/IP into separate weighted signals if DEVICE proves significantly more reliable than IP.

## Risks and Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| Client-reported data is spoofable | Reduced DEVICE signal reliability | Composite hash requires spoofing ALL fields simultaneously; combine with behavioral signals |
| `ClientInfo` format changes across Rust updates | Fingerprint fields become null | Graceful degradation via `fieldMask`; `OnPlayerSetInfo` path is more stable |
| GDPR/privacy concerns with hardware fingerprinting | Legal exposure | All fields hashed by default; `FingerprintEnabled` opt-out; no raw hardware IDs stored |
| High cardinality of deviceHash | Large inverted index | TTL-expire entries after 90 days; only index active players |
| Same household shares hardware | False positive DEVICE links | DEVICE links alone don't flag — require corroborating behavioral or sequence signals via the IdentityDetector's weighted fusion |

## Alternatives Considered

**EAC token correlation.** The `anticheatToken` is per-session and opaque to the server plugin. It cannot be used for cross-session or cross-account correlation.

**Steam API lookups.** Querying Steam's Web API for account creation date, VAC ban history, and friend lists would provide strong signals but introduces an external dependency, rate limits, and requires a Steam API key. This could be a separate enhancement orthogonal to client fingerprinting.

**Packet timing analysis.** Measuring network latency patterns and packet cadence could fingerprint connections, but this requires raw socket access that Oxide plugins don't have.
