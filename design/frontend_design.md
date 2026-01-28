# Behavior Analysis Web App – Frontend Design Document

## 1. Overview

This document outlines the frontend architecture and user interface design for the Behavior Analysis Web App. It complements the `web_app.md` (System Requirements) and `aws_architecture.md` (Backend Infrastructure) by defining *how* investigators will interact with the system.

## 2. Technology Stack

*   **Framework**: React (v18+)
*   **Build Tool**: Vite
*   **Language**: TypeScript
*   **UI Library**: AWS Amplify UI Primitives (`@aws-amplify/ui-react`) for layout and accessible base components.
*   **Styling**: CSS Modules or Tailwind CSS (as per project preference).
*   **State Management**:
    *   **Server State**: Amplify Gen 2 Client (uses internally managed TanStack Query-like caching).
    *   **Local State**: React `useState` / `useReducer` / `Context` for UI state.
*   **Visualization**:
    *   **Charts**: `Recharts` (Timeline, Histograms).
    *   **Graphs**: `react-force-graph` or `react-flow` (Account Linkages).
*   **Routing**: `react-router-dom` (v6).

## 3. Application Structure

```text
src/
├── components/
│   ├── common/           # Buttons, Cards, inputs (wrapped Amplify components)
│   ├── layout/           # Sidebar, Navbar, PageWrapper
│   ├── dashboard/        # Widgets for the main view
│   ├── player/           # Player profile sub-components (Timeline, Stats)
│   └── visual/           # Complex visualizations (Graphs, Charts)
├── pages/
│   ├── Dashboard.tsx
│   ├── PlayerSearch.tsx
│   ├── PlayerProfile.tsx
│   ├── FlagQueue.tsx
│   └── Settings.tsx
├── hooks/                # Custom hooks (e.g., usePlayerRisk, useAuthSession)
├── context/              # Global UI context (Theme, Sidebar state)
├── utils/                # Formatters, helpers
├── App.tsx               # Root with Router and Auth Wrapper
└── main.tsx
```

## 4. UI/UX Flows

### 4.1 Authentication
*   **Mechanism**: `Authenticator` component from Amplify UI.
*   **Flow**: User lands on root -> Redirected to Login -> MFA (if enabled) -> Dashboard.

### 4.2 Main Navigation (Sidebar / Topbar)
*   **Sidebar Items**:
    *   **Dashboard**: High-level overview.
    *   **Search**: Find players by SteamID, IP, or Name.
    *   **Review Queue**: List of flags requiring adjudication.
    *   **System Status**: Health of ingestion pipeline (optional).
*   **Topbar**:
    *   Global Search Bar (Quick jump to player).
    *   User Profile Menu (Sign out).

### 4.3 Dashboard View
**Goal**: At-a-glance situational awareness.
*   **Key Metrics**:
    *   Events Ingested (Last 24h).
    *   Active Flags (High Severity).
    *   Suspected Alt-Accounts Detected.
*   **Activity Feed**: Live stream of high-risk events (via AppSync Subscription).

### 4.4 Player Profile View (`/player/:id`)
**Goal**: The primary forensic workspace.
*   **Header Card**:
    *   Player Name & Avatar (Steam).
    *   **Risk Score** (Large, color-coded badge: 0-100).
    *   Status: `Clean`, `Suspect`, `Banned`.
    *   Actions: `Ban`, `Monitor`, `Clear Flags`.
*   **Tabs**:
    1.  **Overview**:
        *   Summary stats (Headshot %, K/D, Reports).
        *   Active Flags list.
    2.  **Timeline (Forensic)**:
        *   Scatter chart of events over time.
        *   Zoomable interface.
        *   Overlays for "Matches" or "Sessions".
    3.  **Connections (Graph)**:
        *   Node-link diagram showing shared IPs/Hardware.
        *   Nodes: Players, IPs, HWIDs.
        *   Edges: "Shared Session", "Same Info".
    4.  **Raw Data**:
        *   Paginated Data Grid of all telemetry events.
        *   Filterable by Action Type.

### 4.5 Flag Review Queue
**Goal**: Workflow for processing automated alerts.
*   **Interface**: Split view.
    *   **Left**: List of open flags sorted by Severity/Confidence.
    *   **Right**: Preview of the flagged Event/Player context.
*   **Actions**: `Dismiss` (False Positive), `Confirm` (Escalate to Ban), `Defer`.

## 5. Data Integration (Amplify Gen 2)

### 5.1 Client Generation
We utilize the `generateClient` from `aws-amplify/data` to interact with the backend.

```typescript
import { generateClient } from 'aws-amplify/data';
import type { Schema } from '../amplify/data/resource';

const client = generateClient<Schema>();
```

### 5.2 Key Queries
*   `client.models.Player.get({ id: ... })`: Fetch profile.
*   `client.models.Flag.list({ filter: ... })`: Fetch queue.
*   `client.models.Event.list({ ... })`: Fetch timeline data.

### 5.3 Real-time Updates
*   Subscribe to `onCreateFlag` to show toasts/notifications when a new severe anomaly is detected.

## 6. Implementation Plan (Phases)

### Phase 1: MVP (Core Visibility)
*   [ ] Setup React Router & Layout.
*   [ ] Implement Player Search.
*   [ ] Build Basic Player Profile (Overview Tab).
*   [ ] Display Raw Event History List.

### Phase 2: Forensics (Visualization)
*   [ ] Integrate Recharts for Timeline.
*   [ ] Implement Flag Review Queue.
*   [ ] Add basic Account Linking list.

### Phase 3: Advanced
*   [ ] Network Graph visualization.
*   [ ] Real-time WebSocket updates.
*   [ ] Admin actions (Ban command sent back to game server).
