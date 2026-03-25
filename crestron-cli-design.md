---
design:
  project: crestron-cli
  version: 0.1.0
  status: implemented-mvp
  date: 2026-03-04
  requirements_source: /Users/rstanbaugh/.openclaw/tools/crestron/crestron-cli-requirements.md
  implementation_root: /Users/rstanbaugh/.openclaw/tools/crestron
  language: python
  runtime: python3
  priorities:
    - reliable auth/session handling
    - cache-first target resolution
    - deterministic CLI output
    - openclaw-friendly machine output
---

# crestron-cli Detailed Design

## 1. Scope
MVP scope implements:
- authentication and session key flow
- inventory sync (`rooms`, `lights`, `scenes`)
- persisted cache in `state.yaml`
- query commands for lights/rooms/scenes
- light control commands: `on`, `off`, `set`, `toggle`
- scene activation command for both lighting and media scenes

Out of scope for MVP:
- quickactions control
- room filtering
- scene recall command
- cache age expiration policy

## 2. Architecture

```yaml
components:
  cli:
    file: crestron_cli/main.py
    responsibility:
      - parse args
      - dispatch commands
      - select output format
      - map errors to exit code
  config:
    file: crestron_cli/config.py
    responsibility:
      - read env vars
      - validate required config
      - produce base URL and timeout
  api:
    file: crestron_cli/api.py
    responsibility:
      - login/authkey acquisition
      - GET inventory endpoints
      - POST light SetState
      - error normalization
      - auto re-auth on session errors
  state:
    file: crestron_cli/state.py
    responsibility:
      - load/save YAML state
      - build normalized name indexes
      - resolve target name/id
      - render query data models
  utils:
    file: crestron_cli/utils.py
    responsibility:
      - level conversion (0-100 <-> 0-65535)
      - normalization helpers
      - timestamps
      - output serialization helpers
```

## 3. Command Grammar

```yaml
commands:
  initialize:
    syntax: crestron-cli initialize [--force] [--verbose]
    effects:
      - login
      - fetch rooms/lights/scenes
      - write state.yaml
  query_lights:
    syntax: crestron-cli query lights [--refresh] [--json|--yaml]
    effects:
      - read state or refresh
      - render light data
  query_rooms:
    syntax: crestron-cli query rooms [--refresh] [--json|--yaml]
    effects:
      - read state or refresh
      - render room data
  query_scenes:
    syntax: crestron-cli query scenes [--refresh] [--json|--yaml]
    effects:
      - read state or refresh
      - render scene data
      - include scene type in all output formats
  scene_activate:
    syntax: crestron-cli scene <target> activate [--type <lighting|media>] [--room-id <id>] [--json|--yaml]
    effects:
      - resolve scene id/name with optional type disambiguation
      - POST /scenes/recall/{id}
  target_action:
    syntax:
      - crestron-cli <target> on
      - crestron-cli <target> off
      - crestron-cli <target> set <level>
      - crestron-cli <target> toggle
    effects:
      - resolve target via cache
      - POST /lights/SetState
      - update cached level for target
```

## 4. Data Model (`state.yaml`)

```yaml
version: 1
last_refresh: <ISO8601 UTC>
base_url: <string>
auth:
  authkey: <string>
  expires_approx: <string|null>
rooms:
  by_id: {"<id>": {id, name}}
  by_name_normalized: {"<normalized name>": <id>}
lights:
  by_id:
    "<id>":
      id: <int>
      name: <string>
      room_id: <int|null>
      current_level: <int|null>
      percent: <float|null>
      subtype: <string|null>
  by_name_normalized: {"<normalized name>": <id>}
scenes:
  by_id: {"<id>": {id, name, room_id, scene_type, status}}
  by_name_normalized: {"<normalized name>": [<id>, ...]}
quickactions: {}
metadata:
  server_firmware: <string|null>
  last_successful_init: <ISO8601 UTC|null>
  refresh_count: <int>
```

## 5. Authentication / Session Strategy

- Inputs: `CRESTRON_HOME_IP`, `CRESTRON_AUTH_TOKEN`
- Base URL: `http://<CRESTRON_HOME_IP>/cws/api`
- Login request: `GET /login` with base token header
- Extract and store `authkey` from login response
- Include `authkey` and base token in authenticated requests
- If Crestron error source `5001` or `5002` appears:
  - re-login once
  - retry request once

## 6. Cache Strategy

```yaml
policy:
  initialize: always refresh and persist
  query: cache-first, refresh only on --refresh or empty cache
  action: cache-first target resolution
  auth_failure:
    behavior: re-auth + retry; refresh inventory when retry path used by command handlers
```

## 7. Output Strategy

- Human mode (default without machine flags): concise text for terminal
- `--json`: serialized object with `success`, payload data, and metadata
- `--yaml`: YAML object equivalent
- If `OPENCLAW_PY` is present and no format flag was provided, default output format is `yaml`

Error output requirements:
- stderr human-readable error in default mode
- structured failure object in `--json`/`--yaml` mode:
  - `success: false`
  - `error: <message>`
  - `details: <optional>`

## 8. Error Mapping

```yaml
error_source_map:
  5001: Session expired
  5002: Authentication failed
  7003: Lights operation error
```

Unhandled API errors preserve HTTP status and server detail where available.

## 9. Validation Plan

- Syntax checks via `python -m py_compile`
- CLI smoke tests:
  - `crestron-cli.py --help`
  - `crestron-cli.py initialize --help`
  - `crestron-cli.py query lights --help`
- No live API integration test in design-time validation (requires local server/token)

## 10. Extensibility Hooks

- Additional entity domains can be introduced with new top-level state keys
- Query subcommands can extend parser with optional filters (`--room`) later
- Action layer can add scenes/quickactions without changing cache primitives
