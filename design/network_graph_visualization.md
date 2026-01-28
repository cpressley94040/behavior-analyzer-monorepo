# Network Graph Visualization – Design Document

## 1. Overview

### Purpose
Replace the current table-based account connections display with an interactive force-directed graph visualization. This enables investigators to visually explore complex account linkage networks, identify clusters of related accounts, and understand the nature and strength of connections at a glance.

### Goals
1. **Visual Clarity**: Display account relationships as an intuitive node-link diagram
2. **Interactive Exploration**: Enable drilling into connection details, filtering, and navigation
3. **Pattern Recognition**: Help investigators spot account rings, shared infrastructure, and coordinated behavior
4. **Forensic Context**: Integrate with existing player profile and flag data

### Non-Goals
- Real-time graph updates (subscription-based live updates are Phase 2)
- Graph editing/manipulation (read-only visualization)
- Full social network analysis algorithms in frontend (backend provides scores)

---

## 2. Technology Selection

### Recommendation: `react-force-graph-2d`

| Library | Pros | Cons | Verdict |
|---------|------|------|---------|
| **react-force-graph-2d** | Fast rendering (Canvas), built-in forces, zoom/pan, node clustering | Less customizable than SVG | **Selected** |
| react-force-graph-3d | 3D visualization, immersive | Overkill for this use case, higher complexity | Not selected |
| react-flow | Highly customizable, drag-drop, SVG-based | Designed for flowcharts, not force-directed graphs | Not selected |
| d3-force + React | Maximum flexibility | Requires manual integration, more code | Fallback option |
| vis-network | Full-featured | Large bundle, dated API | Not selected |

### Justification
- `react-force-graph-2d` uses HTML5 Canvas for efficient rendering of 100s-1000s of nodes
- Built-in physics simulation (d3-force under the hood)
- Supports node/link styling, hover, click, zoom, pan out of the box
- Smaller bundle than alternatives (~50KB gzipped)
- Active maintenance and TypeScript support

---

## 3. Data Model

### 3.1 Graph Data Structure

```typescript
// Types for the graph visualization
interface GraphNode {
  id: string;                    // Unique identifier
  type: 'player' | 'ip' | 'hwid' | 'session';
  label: string;                 // Display name

  // Player-specific fields
  playerId?: string;
  riskScore?: number;            // 0-100
  status?: 'CLEAN' | 'SUSPECT' | 'BANNED' | 'MONITOR';
  flagsOpen?: number;

  // Identifier-specific fields
  hash?: string;                 // For IP/HWID nodes
  firstSeen?: number;
  lastSeen?: number;

  // Visual properties (computed)
  size?: number;                 // Node radius based on connections
  color?: string;                // Based on type/risk
}

interface GraphLink {
  source: string;                // Node ID
  target: string;                // Node ID
  signalType: 'IP' | 'HWID' | 'BEHAVIOR' | 'SEQUENCE' | 'SESSION';
  confidence: number;            // 0.0 - 1.0
  lastSeen?: number;

  // Visual properties (computed)
  width?: number;                // Based on confidence
  color?: string;                // Based on signal type
  curvature?: number;            // For multiple edges between nodes
}

interface GraphData {
  nodes: GraphNode[];
  links: GraphLink[];
}
```

### 3.2 Backend Query Requirements

The graph requires an expanded query that returns not just direct connections, but the full linkage graph for a player:

```graphql
# New query to add to AppSync schema
type Query {
  getPlayerConnectionGraph(
    playerId: String!
    depth: Int = 2           # How many hops from source player
    minConfidence: Float = 0.3
  ): ConnectionGraph
}

type ConnectionGraph {
  nodes: [GraphNode!]!
  links: [GraphLink!]!
  metadata: GraphMetadata
}

type GraphNode {
  id: ID!
  type: NodeType!
  label: String!
  playerId: String
  riskScore: Float
  status: PlayerStatus
  flagsOpen: Int
  hash: String
  firstSeen: Float
  lastSeen: Float
}

type GraphLink {
  source: ID!
  target: ID!
  signalType: SignalType!
  confidence: Float!
  lastSeen: Float
}

type GraphMetadata {
  totalNodes: Int!
  totalLinks: Int!
  clusters: Int!           # Number of disconnected components
  maxRiskScore: Float!
  queriedAt: Float!
}

enum NodeType {
  PLAYER
  IP
  HWID
  SESSION
}

enum SignalType {
  IP
  HWID
  BEHAVIOR
  SEQUENCE
  SESSION
}
```

---

## 4. Visual Design

### 4.1 Node Styling

| Node Type | Shape | Base Color | Size Formula |
|-----------|-------|------------|--------------|
| Player | Circle | Risk-based gradient | `10 + (connections * 2)` |
| IP | Hexagon | `#6366f1` (indigo) | `8 + (connections * 1.5)` |
| HWID | Square | `#8b5cf6` (violet) | `8 + (connections * 1.5)` |
| Session | Diamond | `#06b6d4` (cyan) | `6` (fixed) |

#### Player Node Color Scale (by Risk Score)
```
0-20:   #22c55e (green)    - Clean
21-40:  #84cc16 (lime)     - Low risk
41-60:  #eab308 (yellow)   - Moderate
61-80:  #f97316 (orange)   - High risk
81-100: #ef4444 (red)      - Critical
```

#### Player Status Indicators
- **BANNED**: Red border ring + strikethrough icon
- **SUSPECT**: Orange pulsing border
- **MONITOR**: Blue dashed border
- **CLEAN**: No additional indicator

### 4.2 Link Styling

| Signal Type | Color | Style |
|-------------|-------|-------|
| IP | `#6366f1` (indigo) | Solid |
| HWID | `#8b5cf6` (violet) | Solid |
| BEHAVIOR | `#f59e0b` (amber) | Dashed |
| SEQUENCE | `#10b981` (emerald) | Dotted |
| SESSION | `#06b6d4` (cyan) | Solid thin |

#### Link Width (by Confidence)
```
width = 1 + (confidence * 4)
// Range: 1px (30% confidence) to 5px (100% confidence)
```

#### Link Opacity
```
opacity = 0.3 + (confidence * 0.7)
// Range: 0.3 (30% confidence) to 1.0 (100% confidence)
```

### 4.3 Layout

```
+------------------------------------------------------------------+
|  [← Back to Profile]     Account Network Graph                    |
+------------------------------------------------------------------+
|                                                                   |
|  +------------------+  +--------------------------------------+   |
|  | FILTERS          |  |                                      |   |
|  | □ Show IPs       |  |                                      |   |
|  | □ Show HWIDs     |  |          GRAPH CANVAS                |   |
|  | □ Show Sessions  |  |                                      |   |
|  |                  |  |     (Force-directed visualization)   |   |
|  | Confidence: ──●──|  |                                      |   |
|  | [0.3]      [1.0] |  |                                      |   |
|  |                  |  |                                      |   |
|  | LEGEND           |  |                                      |   |
|  | ● Player         |  |                                      |   |
|  | ⬡ IP Hash        |  |                                      |   |
|  | ■ HWID           |  |                                      |   |
|  | ◆ Session        |  |                                      |   |
|  |                  |  |                                      |   |
|  | STATS            |  |                                      |   |
|  | Nodes: 24        |  |                                      |   |
|  | Links: 47        |  |                                      |   |
|  | Clusters: 2      |  +--------------------------------------+   |
|  +------------------+                                             |
|                                                                   |
|  +---------------------------------------------------------------+|
|  | SELECTED NODE DETAILS                                          |
|  | Player: 76561198xxxxx | Risk: 72 | Status: SUSPECT | Flags: 3  |
|  | [View Profile] [View Flags] [View Timeline]                    |
|  +---------------------------------------------------------------+|
+------------------------------------------------------------------+
```

---

## 5. Interactions

### 5.1 Node Interactions

| Action | Behavior |
|--------|----------|
| **Hover** | Highlight node + connected edges, show tooltip with summary |
| **Click** | Select node, show details panel, center view on node |
| **Double-click** | Navigate to player profile (if player node) |
| **Right-click** | Context menu: View Profile, View Flags, Expand Connections |

### 5.2 Canvas Interactions

| Action | Behavior |
|--------|----------|
| **Drag background** | Pan the view |
| **Scroll wheel** | Zoom in/out |
| **Drag node** | Move node, pause physics for that node |
| **Pinch (touch)** | Zoom on touch devices |

### 5.3 Filter Controls

| Control | Effect |
|---------|--------|
| **Node type toggles** | Show/hide IP, HWID, Session nodes |
| **Confidence slider** | Filter links below threshold |
| **Search box** | Highlight nodes matching player ID/name |
| **Depth selector** | 1-hop, 2-hop, 3-hop from center player |

### 5.4 Tooltips

**Player Node Tooltip:**
```
┌─────────────────────────────┐
│ Player: SteamName           │
│ ID: 76561198xxxxxxxx        │
│ Risk Score: 72/100          │
│ Status: SUSPECT             │
│ Open Flags: 3               │
│ Connections: 5              │
│ Last Seen: 2 hours ago      │
└─────────────────────────────┘
```

**Link Tooltip:**
```
┌─────────────────────────────┐
│ Connection Type: IP         │
│ Confidence: 87%             │
│ First Seen: Jan 15, 2026    │
│ Last Seen: 2 hours ago      │
└─────────────────────────────┘
```

---

## 6. Component Architecture

### 6.1 Component Hierarchy

```
<PlayerProfile>
  └── <PlayerConnectionsTab>
        ├── <ConnectionsTableView>     // Existing table (toggle option)
        └── <ConnectionsGraphView>     // New graph visualization
              ├── <GraphControls>
              │     ├── <NodeTypeFilters>
              │     ├── <ConfidenceSlider>
              │     ├── <DepthSelector>
              │     └── <SearchBox>
              ├── <GraphCanvas>         // react-force-graph-2d wrapper
              ├── <GraphLegend>
              ├── <GraphStats>
              └── <NodeDetailPanel>
```

### 6.2 State Management

```typescript
interface GraphViewState {
  // Data
  graphData: GraphData | null;
  isLoading: boolean;
  error: Error | null;

  // Filters
  showIPs: boolean;
  showHWIDs: boolean;
  showSessions: boolean;
  minConfidence: number;
  depth: 1 | 2 | 3;
  searchQuery: string;

  // Selection
  selectedNodeId: string | null;
  hoveredNodeId: string | null;
  highlightedLinks: Set<string>;

  // View
  viewMode: 'graph' | 'table';
  zoomLevel: number;
  centerCoords: { x: number; y: number };
}
```

### 6.3 Custom Hooks

```typescript
// Fetch and transform graph data
function useConnectionGraph(playerId: string, depth: number, minConfidence: number) {
  const [data, setData] = useState<GraphData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);

  useEffect(() => {
    // Fetch from AppSync, transform to GraphData format
  }, [playerId, depth, minConfidence]);

  return { data, loading, error, refetch };
}

// Filter graph data based on UI controls
function useFilteredGraph(
  data: GraphData | null,
  filters: FilterState
): GraphData | null {
  return useMemo(() => {
    if (!data) return null;
    // Apply node type and confidence filters
  }, [data, filters]);
}

// Track node selection and highlighting
function useGraphSelection(graphRef: RefObject<ForceGraphMethods>) {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [hoveredId, setHoveredId] = useState<string | null>(null);

  const centerOnNode = useCallback((nodeId: string) => {
    // Use graphRef to center view
  }, [graphRef]);

  return { selectedId, setSelectedId, hoveredId, setHoveredId, centerOnNode };
}
```

---

## 7. Performance Considerations

### 7.1 Rendering Optimization

| Concern | Mitigation |
|---------|------------|
| Large graphs (>500 nodes) | Use Canvas renderer (default), not SVG |
| Frequent re-renders | Memoize node/link paint functions |
| Initial layout time | Show loading spinner, use warmup ticks |
| Mobile performance | Reduce particle effects, simplify on touch |

### 7.2 Data Optimization

```typescript
// Limit graph size on backend
const MAX_NODES = 200;
const MAX_DEPTH = 3;

// Pagination for very large networks
interface PaginatedGraphQuery {
  playerId: string;
  depth: number;
  minConfidence: number;
  limit: number;           // Max nodes to return
  cursor?: string;         // For pagination
  prioritize: 'risk' | 'recency' | 'confidence';
}
```

### 7.3 Progressive Loading

1. **Initial load**: Center player + direct connections (depth=1)
2. **On expand**: Fetch additional depth incrementally
3. **Lazy load details**: Fetch full player data on hover/select

---

## 8. Accessibility

### 8.1 Keyboard Navigation

| Key | Action |
|-----|--------|
| `Tab` | Cycle through nodes |
| `Enter` | Select focused node |
| `Escape` | Deselect, close panels |
| `+` / `-` | Zoom in/out |
| Arrow keys | Pan view |
| `/` | Focus search box |

### 8.2 Screen Reader Support

- Provide text summary of graph statistics
- Announce node details on focus
- Offer table view as accessible alternative
- Use ARIA labels for controls

### 8.3 Color Blindness

- Use patterns (dashed, dotted) in addition to colors for link types
- Provide high-contrast mode option
- Node shapes differentiate types (not just color)

---

## 9. Implementation Plan

### Phase 1: Core Visualization (MVP)
- [ ] Install `react-force-graph-2d` dependency
- [ ] Create `ConnectionsGraphView` component
- [ ] Implement basic node/link rendering with styling
- [ ] Add zoom, pan, and node selection
- [ ] Show node details on click
- [ ] Add toggle between table and graph views

### Phase 2: Filtering & Controls
- [ ] Implement node type filter toggles
- [ ] Add confidence threshold slider
- [ ] Add depth selector (1/2/3 hops)
- [ ] Implement search/highlight functionality
- [ ] Add legend component

### Phase 3: Backend Integration
- [ ] Create `getPlayerConnectionGraph` resolver
- [ ] Implement graph traversal Lambda function
- [ ] Add caching for computed graphs
- [ ] Optimize query performance

### Phase 4: Polish & Performance
- [ ] Add loading states and skeletons
- [ ] Implement progressive loading for large graphs
- [ ] Add keyboard navigation
- [ ] Performance testing with large datasets
- [ ] Mobile touch optimization

### Phase 5: Advanced Features (Future)
- [ ] Real-time updates via subscriptions
- [ ] Graph export (PNG, SVG)
- [ ] Cluster detection highlighting
- [ ] Timeline animation (show network evolution)
- [ ] Comparison view (two players' networks side-by-side)

---

## 10. Dependencies

### New NPM Packages

```json
{
  "dependencies": {
    "react-force-graph-2d": "^1.25.0",
    "d3-scale-chromatic": "^3.0.0"
  },
  "devDependencies": {
    "@types/d3-scale-chromatic": "^3.0.0"
  }
}
```

### Estimated Bundle Impact
- `react-force-graph-2d`: ~50KB gzipped (includes d3-force)
- `d3-scale-chromatic`: ~10KB gzipped
- **Total**: ~60KB additional bundle size

---

## 11. Testing Strategy

### Unit Tests
- Graph data transformation functions
- Filter logic
- Color/size computation utilities

### Integration Tests
- Graph renders with mock data
- Filters update graph correctly
- Navigation to player profile works

### Visual Regression Tests
- Screenshot comparison for different graph states
- Responsive layout at different viewports

### Performance Tests
- Render time with 100, 500, 1000 nodes
- Memory usage monitoring
- Frame rate during interaction

---

## 12. Success Metrics

| Metric | Target |
|--------|--------|
| Time to identify linked accounts | 50% reduction vs table view |
| Investigator satisfaction (survey) | 4.0+ / 5.0 |
| Graph render time (100 nodes) | < 500ms |
| Frame rate during interaction | 60fps on desktop, 30fps on mobile |
| Accessibility audit score | WCAG 2.1 AA compliant |

---

## 13. Open Questions

1. **Should IP/HWID nodes show actual hashed values?**
   - Privacy consideration: hashes are one-way but still linkable
   - Recommendation: Show truncated hash (first 8 chars) + count of associated players

2. **How to handle very dense networks (>500 nodes)?**
   - Options: Server-side aggregation, clustering, pagination
   - Recommendation: Implement "smart collapse" to group low-risk peripheral nodes

3. **Should we support graph export for reports?**
   - Useful for ban appeals and documentation
   - Recommendation: Add in Phase 5 with PNG/SVG export

4. **Real-time updates priority?**
   - New connections could appear during investigation
   - Recommendation: Defer to Phase 5, manual refresh for MVP
