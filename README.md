# crestron-cli

Lightweight CLI for Crestron Home control with cache-backed targeting.

Current MVP supports:
- initialize and cache inventory (`rooms`, `lights`, `scenes`, `speakers`)
- query lights/rooms/scenes/audio
- light actions (`on`, `off`, `set`, `toggle`)
- scene activation (`scene <target> on|activate`) for lighting and media scenes
- audio actions (`audio <target> on|off|set|mute|unmute|toggle|source`)

Action semantics (all systems):
- lights: `toggle` toggles light power intent (`on`/`off`) based on current level
- scenes: `on` and `activate` are equivalent scene recall actions
- audio rooms: `toggle` toggles power (`on`/`off`), while `mute`/`unmute` control mute explicitly

Audio/source defaults:
- `audio <target> on` now defaults to Player A when `--player` is omitted
- global player sources are shared (`Player A`, `Player B`) and affect any room routed to that player
- use `crestron-cli query audio` to inspect room audio state (including current player)
- use `crestron-cli query audio player` to inspect current Player A/B sources
- use `crestron-cli query audio source` to list available sources and IDs
- use `crestron-cli audio <A|B>=<source-id|source-name>` to set global player source (name matching is case-insensitive and supports partial text)

## AI/Agent Usage (Recommended)

For OpenClaw or other automation agents, use structured output and parse only stable fields.

- Always pass `--yaml` or `--json` (do not parse human text mode)
- Treat exit code `0` as success and non-zero as failure
- In structured modes, parse `success` first
- On failures, parse `error` and optional `details`
- Use `--refresh` on query commands when fresh inventory is required

Suggested command sequence for agents:

```bash
crestron-cli initialize --yaml
crestron-cli query rooms --yaml
crestron-cli query lights --yaml
crestron-cli query audio --yaml
crestron-cli light "Kitchen Island" set 35 --yaml
```

Structured response shape (stable contract):

- initialize: `success`, `message`, `data.rooms`, `data.lights`, `data.scenes`, `data.speakers`, `data.state_path`
- query lights|rooms|scenes|audio: `success`, `entity`, `count`, `refreshed`, `items[]` (`query audio` returns room audio state, `query audio player` returns Player A/B mapping, `query audio source` returns sources)
- light action (on/off/set/toggle): `success`, `message`, `data.id`, `data.name`, `data.action`, `data.current_state`, `data.requested_level_raw`, `data.requested_level_percent`, `data.level_raw`, `data.level_percent`, `data.observed_from_refresh`
- scene action (on/activate): `success`, `message`, `data.id`, `data.name`, `data.action`, `data.current_state`, `data.scene_type`, `data.room_id`
- audio action (on/off/set/mute/unmute/toggle/source): `success`, `message`, `data.id`, `data.name`, `data.action`, `data.current_state`, `data.room_id`, optional `data.level_percent`, `data.source_id`, `data.source_name`, `data.player`, `data.current_power_state`, `data.current_mute_state`, `data.current_source_id`, `data.current_source_name`, `data.current_player`, `data.observed_from_refresh`
- audio player assignment (`audio A=<source>`): `success`, `message`, `data.player`, `data.source_id`, `data.source_name`
- errors: `success: false`, `error`, optional `details`

## Runtime model

- Primary runtime: conda env `openclaw`
- Primary entrypoint: `~/bin/crestron-cli` (thin shell wrapper)
- Core program: `~/.openclaw/tools/crestron/crestron-cli.py`

The wrapper should only load env vars and dispatch to Python.

## Dependencies (conda)

Install in `openclaw`:

```bash
conda install -n openclaw -y requests pyyaml
```

## Environment variables

Defined in `~/.openclaw/.env`:

- `CRESTRON_HOME_IP` (required)
- `CRESTRON_AUTH_TOKEN` (required)
- `CRESTRON_TIMEOUT_S` (optional, default `10`)
- `OPENCLAW_PY` (optional; defaults to `python3` in wrapper)

Example:

```dotenv
CRESTRON_HOME_IP=192.168.0.201
CRESTRON_AUTH_TOKEN=YOUR_BASE_TOKEN
CRESTRON_TIMEOUT_S=10
OPENCLAW_PY=/opt/homebrew/Caskroom/miniforge/base/envs/openclaw/bin/python
```

## `~/bin/crestron-cli` wrapper

```bash
#!/usr/bin/env bash
set -euo pipefail

ENV_FILE="$HOME/.openclaw/.env"
if [[ -f "$ENV_FILE" ]]; then
	set -a
	# shellcheck disable=SC1090
	source "$ENV_FILE"
	set +a
fi

PY="${OPENCLAW_PY:-python3}"
TOOL="$HOME/.openclaw/tools/crestron/crestron-cli.py"

exec "$PY" "$TOOL" "$@"
```

## Commands

```text
crestron-cli initialize [--force] [--verbose] [--json|--yaml]
crestron-cli query [lights|scenes|audio] [room=<id|name>] [player|source] [--refresh] [--raw|--json|--yaml]
crestron-cli query room=<id|name> [lights|scenes|audio] [player|source] [--refresh] [--raw|--json|--yaml]
crestron-cli query rooms [--refresh] [--raw|--json|--yaml]
crestron-cli query audio [room=<id|name>|player|source] [--refresh] [--raw|--json|--yaml]
crestron-cli scene <target> {on|activate} [--type <lighting|media>] [--room-id <id>] [--json|--yaml]
crestron-cli audio <target> {on|off|set|mute|unmute|toggle|source} [value] [--player <A|B>] [--json|--yaml]
crestron-cli audio <A|B>=<source-id|source-name>
crestron-cli light <target> {on|off|set|toggle} [value] [--json|--yaml]
```

Examples:

```bash
crestron-cli query
crestron-cli query lights --raw
crestron-cli query lights room=10
crestron-cli query lights room='Man Cave'
crestron-cli query room=10 lights
crestron-cli query room='man cave' lights
crestron-cli query scenes room=10
crestron-cli query room=10 scenes --raw
crestron-cli query audio --yaml
crestron-cli query audio room='man cave' --yaml
crestron-cli query audio player --yaml
crestron-cli query audio source --yaml
crestron-cli light "Kitchen Island" set 35 --yaml
crestron-cli light id=1135 toggle --yaml
crestron-cli scene "Happy Hour" on --type media --yaml
crestron-cli scene id=52138 activate --yaml
crestron-cli audio "Kitchen" on --yaml
crestron-cli audio "Kitchen" on --player A --yaml
crestron-cli audio "Kitchen" set 35 --yaml
crestron-cli audio "Kitchen" toggle --yaml
crestron-cli audio "Kitchen" source "Player B Spotify" --player B --yaml
crestron-cli audio A="Spotify" --yaml
crestron-cli audio B=52312 --yaml
```

### Target syntax

Supported target formats:
- numeric id: `1135`
- id token: `id=1135`
- cached name: `"Billiards Table"`

If multiple lights share the same name, name targeting is ambiguous and the CLI will require `id=...`.

Examples:

```bash
crestron-cli light id=1135 toggle --yaml
crestron-cli light id=1135 set 50 --yaml
crestron-cli light "Billiards Table" off --yaml
```

## State cache

State file:

```text
~/.openclaw/tools/crestron/state.yaml
```

Behavior:
- `initialize` always refreshes and rebuilds state maps
- `query` is cache-first unless `--refresh` is supplied
- actions resolve targets from cache and update cache after success

## Output modes

- query default: human-readable table
- `--raw`: CSV (comma-separated values)
- `--json`: structured JSON
- `--yaml`: structured YAML

For `query scenes`, all output modes include `scene_type`.
