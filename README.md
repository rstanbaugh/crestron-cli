# crestron-cli

Lightweight CLI for Crestron Home control with cache-backed targeting.

Current MVP supports:
- initialize and cache inventory (`rooms`, `lights`, `scenes`)
- query lights/rooms/scenes
- light actions (`on`, `off`, `set`, `toggle`)

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
crestron-cli "Kitchen Island" set 35 --yaml
```

Structured response shape (stable contract):

- initialize: `success`, `message`, `data.rooms`, `data.lights`, `data.scenes`, `data.state_path`
- query lights|rooms|scenes: `success`, `entity`, `count`, `refreshed`, `items[]`
- actions (on/off/set/toggle): `success`, `message`, `data.id`, `data.name`, `data.action`, `data.level_raw`, `data.level_percent`
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
crestron-cli query [lights|scenes] [room=<id>] [--refresh] [--raw|--json|--yaml]
crestron-cli query room=<id> [lights|scenes] [--refresh] [--raw|--json|--yaml]
crestron-cli query rooms [--refresh] [--raw|--json|--yaml]
crestron-cli <target> on [--json|--yaml]
crestron-cli <target> off [--json|--yaml]
crestron-cli <target> set <level> [--json|--yaml]
crestron-cli <target> toggle [--json|--yaml]
```

Examples:

```bash
crestron-cli query
crestron-cli query lights --raw
crestron-cli query lights room=10
crestron-cli query room=10 lights
crestron-cli query scenes room=10
crestron-cli query room=10 scenes --raw
```

### Target syntax

Supported target formats:
- numeric id: `1135`
- id token: `light=1135` or `id=1135`
- cached name: `"Billiards Table"`

Examples:

```bash
crestron-cli light=1135 toggle --yaml
crestron-cli light=1135 set 50 --yaml
crestron-cli "Billiards Table" off --yaml
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
