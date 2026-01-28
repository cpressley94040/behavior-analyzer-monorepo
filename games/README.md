# Games Integration

This directory contains game-specific integrations for the Behavior Analyzer platform. Each subdirectory provides a plugin or client library for capturing behavioral telemetry from a specific game.

## Available Integrations

### Rust (rust_plugin/)

An Oxide/uMod plugin for Rust dedicated servers that captures server-side gameplay telemetry.

**Features:**
- Hooks into 20+ Oxide events (combat, looting, chat, connections)
- Asynchronous GraphQL API forwarding
- Steam ID tracking for player identification
- OAuth authentication with the analysis backend

**Quick Start:**
```bash
# Run tests
cd rust_plugin/test
/usr/local/share/dotnet/dotnet test

# Deploy plugin
cp rust_plugin/BehaviorAnalyzer.cs <rust_server>/oxide/plugins/
```

See [rust_plugin/README.md](rust_plugin/README.md) for detailed documentation.

## Adding New Game Integrations

To add support for a new game:

1. Create a subdirectory named after the game (e.g., `csgo/`, `valorant/`)
2. Implement telemetry capture using the game's modding/plugin API
3. Format events according to the Behavior Analyzer event schema
4. Forward events to the GraphQL API endpoint
5. Add a README documenting installation and configuration

### Event Schema

All game integrations should forward events with this structure:

```json
{
  "userId": "player_unique_id",
  "actionType": "EventType",
  "details": {
    "game_specific_field": "value"
  },
  "timestamp": "2025-01-17T12:00:00Z"
}
```

### Common Action Types

| Action Type | Description |
|-------------|-------------|
| `PlayerConnect` | Player joined the server |
| `PlayerDisconnect` | Player left the server |
| `PlayerAttack` | Combat engagement |
| `PlayerDeath` | Player death event |
| `Chat` | In-game chat message |
| `Loot` | Item/container interaction |

## Requirements

Requirements vary by game integration. See individual plugin READMEs for specific dependencies.
