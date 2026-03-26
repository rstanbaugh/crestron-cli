# Crestron CLI Enhancements for Home-State Integration (Draft v1)

## Purpose
Define Crestron-specific additions needed to integrate cleanly with the home-state tool and house-management skill architecture.

## Current Capability Baseline
Already available in crestron-cli:
- initialize cache
- query rooms/lights/scenes
- light actions on/off/set/toggle
- structured output in yaml/json

## Required Enhancements

### 1) Scene Activation Command
Add direct scene execution support.

Proposed command:
- crestron-cli scene <target> activate [--json|--yaml]

Target formats:
- numeric id
- scene=<id>
- scene name from cache

Structured success shape:
- success
- message
- data.id
- data.name
- data.action=activate
- data.room_id (optional)

### 2) Normalized Export for Adapter Ingest
Add a stable export for home-state adapter ingestion.

Proposed command:
- crestron-cli export snapshot --yaml
- crestron-cli export snapshot --json

Export includes:
- rooms
- lights
- scenes
- service ids
- last refresh timestamp

Requirements:
- no credentials or authkey in export payload

### 3) Change Event Emission Hook
Provide optional event output after successful actions/refreshes.

Proposed options:
- --emit-event-json (stdout side channel) or
- --event-file <path>

Event types:
- refresh.completed
- device.state.changed
- scene.activated
- action.failed

### 4) Query by Explicit Id (Optional but Useful)
Proposed support:
- crestron-cli query lights id=<light_id> --yaml
- crestron-cli query scenes id=<scene_id> --yaml

Reason:
- improves deterministic post-action verification by adapter and planner

## Freshness Alignment Requirements
- query commands should preserve refreshed=true/false semantics
- adapter can trigger --refresh when state is stale by policy (>5m)
- action flows should support pre-action verification and post-action readback

## Error Contract Requirements
- continue using success=false, error, details in structured output
- include consistent failure details for per-target reporting in multi-step execution

## Acceptance Criteria
- scene activation available via CLI command
- export snapshot is stable, normalized, and credential-free
- events can be emitted for refresh/action transitions
- outputs remain deterministic for home-state adapter consumption
