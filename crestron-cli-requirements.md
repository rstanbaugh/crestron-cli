# crestron-cli Requirements Specification

**Project Name**: crestron-cli  
**Purpose**: A lightweight Python command-line interface to interact with a Crestron Home server (primarily lighting control at launch).  
**Usage Contexts**:
- Standalone terminal tool  
- Executed as a tool/skill by openclaw agents via `exec "$PY" "$TOOL" "$@"`

**Current Date of Spec**: March 2026  
**Target Environment**: Python in conda env named `openclaw`

## 1. Project Folder Structure
```
~/.openclaw/tools/crestron/
├── crestron-cli                  # executable entry-point script (short, dispatches to package)
├── pyproject.toml                # defines project name, version, dependencies
├── state.yaml                    # persistent cache (rooms, lights, scenes, name→id maps, etc.)
├── README.md                     # usage notes, env vars, examples
└── crestron_cli/                 # importable Python package (flat, no src/)
├── init.py               # package marker (can include version)
├── main.py               # optional: supports python -m crestron_cli
├── main.py                   # argument parsing, command dispatch, main flow
├── config.py                 # environment variable loading & constants
├── api.py                    # HTTP requests, auth, error mapping
├── state.py                  # load/save YAML cache, target resolution
└── utils.py                  # helpers (level scaling, output formatting, etc.)
```


## 2. Dependencies

Install in conda env `openclaw`:

- `requests`     (HTTP client)
- `pyyaml`       (state file & YAML output)

No other dependencies for MVP (no typer, rich, pydantic, httpx, etc.).

## 3. Environment Variables

| Variable                  | Required? | Default          | Purpose                                      |
|---------------------------|-----------|------------------|----------------------------------------------|
| `CRESTRON_HOME_IP`        | Yes       | —                | Server IP/hostname                           |
| `CRESTRON_AUTH_TOKEN`     | Yes       | —                | Long-lived base token from Web API Settings  |
| `CRESTRON_TIMEOUT_S`      | No        | 10               | HTTP request timeout (seconds)               |
| `OPENCLAW_PY`             | No        | `python3`        | Used by openclaw to select Python interpreter |

## 4. State Database (`state.yaml`)

Location: `~/.openclaw/tools/crestron/state.yaml`

Structure (extensible design):

```yaml
version: 1
last_refresh: "2026-03-04T22:13:45Z"   # ISO 8601 UTC
base_url: "http://192.168.0.201/cws/api"
auth:
  authkey: "nfLLZa6etB5q"
  expires_approx: "2026-03-05T06:13:45Z"   # optional
rooms:
  by_id:
    "42":
      id: 42
      name: "Kitchen"
      # future: description, type, icon, order
  by_name_normalized:
    "kitchen": 42
lights:
  by_id:
    "1141":
      id: 1141
      name: "Kitchen Island"
      room_id: 42
      current_level: 48316          # raw 0–65535
      percent: 73.7                 # computed
      subtype: "dimmer"
  by_name_normalized:
    "kitchen island": 1141
scenes:
  by_id:
    "52060":
      id: 52060
      name: "Movie Night"
      room_id: 42
  by_name_normalized:
    "movie night": 52060
quickactions: {}                     # stub for future
metadata:
  server_firmware: "3.021.0214"      # optional
  last_successful_init: "..."
  refresh_count: 3
```
Rooms are first-class and referenced by room_id in other entities.
by_name_normalized uses lowercase, stripped keys for case-insensitive matching.
Designed to add new entity types (shades, thermostats, etc.) as new top-level keys without breaking existing code.

## 5. MVP Commands – v0.1 (Lights-focused)
Flat command style:
```
crestron-cli initialize [--force] [--verbose]
crestron-cli query lights [--refresh] [--json|--yaml]
crestron-cli query scenes [--refresh] [--json|--yaml]
crestron-cli <target> on
crestron-cli <target> off
crestron-cli <target> set <level>     # level = 0–100 integer
crestron-cli <target> toggle
```
`<target>`: numeric ID or name (resolved from cache)
Name resolution: exact match (case-insensitive) via `by_name_normalized`
`initialize`:
Validate base token
GET /login → obtain authkey
GET /rooms, /lights, /scenes
Build lookup maps
Save state.yaml
Print summary (room/light/scene counts)

`query lights`: name, id, room name/id, current level (raw + %)
`query scenes`: name, id
Actions: POST /lights/SetState
on/off: level 65535 / 0
set: scale 0–100 → 0–65535
toggle: invert current level

Output formats:
Default: human-readable indented text
`--json`: structured dict
`--yaml`: structured YAML
If OPENCLAW_PY in env → default to --yaml if no format flag

## 6. Error Handling & Output Rules

Exit 0 → success only
Exit 1 → any failure
Errors to stderr (human message + suggestion)
In --json/--yaml mode: include `{"success": false, "error": "...", "details": "..."}`
Map Crestron errorSource where possible:
5001: Session expired → auto re-auth
5002: Authentication failed
7003: Lights operation error
etc.

Success messages: "Kitchen Island turned on", "Level set to 75%"

## 7. Refresh & Cache Policy (MVP)

Cache-first for queries/actions
Force refresh on:
`initialize` (always)
`--refresh` flag
Auth failure (5001/5002) → re-auth + refresh

No automatic age-based refresh in MVP

## 8. Post-MVP Features (Planned Iterations)

Quick actions support (`trigger, query quickactions`)
Scene recall command
Room filtering (`query lights --room "Kitchen"`)
Rich output (tables/colors when no format flag)
Cache age expiration (`CRESTRON_CACHE_HOURS`)
More actions: dim, ramp time `--time <ms>`
Verbose/debug mode
Map additional errorSource codes
openclaw optimizations (minimal output, no colors)
Future: shades, thermostats, media rooms, event subscriptions

## 9. Open Questions / TBD

Add `--room` filter to `query` in MVP or post-MVP?
Include scene recall in MVP?
Add quickactions to MVP?
Final decision on verbose logging level/format

## 10. Prototype Crestron Requests
- Insomnia routes in ~/openclaw/tools/crestron/Insomnia_2026-03-04.yaml
- these routes have been tested

## API Documents
The official API documentation for the Crestron Home server (Crestron Home OS) is a REST API, hosted on Crestron's developer portal.
Primary Resource – REST API for Crestron Home® OS

Main index: https://sdkcon78221.crestron.com/sdk/Crestron-Home-API/index.htm
This is the core developer documentation. It covers modifying system settings, viewing status, and making API calls. It's current as of firmware version 3.021.0214 (may have updates since).
Key sections include:
Quick Start / Overview: https://sdkcon78221.crestron.com/sdk/Crestron-Home-API/Content/Topics/Quick-Start/Overview.htm
Base URI: https://{host}/cws/api (where {host} is your Crestron Home server's IP or hostname).
Uses GET/POST methods with JSON payloads.
Authentication requires a token (Crestron-RestAPI-AuthToken from Web API Settings in the Crestron Home interface, then login to get an auth key).

API Reference: https://sdkcon78221.crestron.com/sdk/Crestron-Home-API/Content/Topics/API-Reference/API-Reference.htm
Detailed endpoints for rooms, devices, media rooms, loads, scenes, etc.
Examples: /cws/api/devices, /cws/api/rooms, /cws/api/login, etc.

Other useful pages:
Devices API: https://sdkcon78221.crestron.com/sdk/Crestron-Home-API/Content/Topics/API-Reference/Devices-API.htm
Rooms API: https://sdkcon78221.crestron.com/sdk/Crestron-Home-API/Content/Topics/API-Reference/Rooms-API.htm
Login API: https://sdkcon78221.crestron.com/sdk/Crestron-Home-API/Content/Topics/API-Reference/Login-API.htm



Authentication Notes (critical for crestron-cli)

Enable and generate/manage the Web API token in the Crestron Home UI: Settings > System Control Options > Web API Settings (see https://docs.crestron.com/en-us/8525/Content/CP4R/Installer-Settings/Sys-Cont/Web-API.htm).
Use HTTP Basic auth over SSL or the token-based flow to obtain a session auth key.

Developer Portal Home (for context and other Crestron APIs)

https://sdkcon78221.crestron.com/
Lists this REST API among others (e.g., XiO Cloud, drivers SDK).


No public evidence of a native WebSocket API for real-time events in the standard Crestron Home REST docs—it's primarily RESTful with synchronous/async GET/POST support. If polling isn't sufficient for your use case (e.g., live feedback in openclaw agents), we may need to explore event subscriptions via specific endpoints or check for firmware updates.
I've added these to our project resources for crestron-cli. For implementation, we'll likely start with:

Discovering the server (local network scan or user-provided IP).
Handling token auth flow.
Core commands like list rooms/devices, get/set status, invoke scenes/actions.

Let me know the next priority—e.g., skeleton CLI structure with click or argparse, initial auth implementation, or endpoint exploration with sample requests? I can draft code snippets accordingly.