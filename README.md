# crestron-cli

Lightweight CLI for Crestron Home control with cache-backed targeting.

Current MVP supports:
- initialize and cache inventory (`rooms`, `lights`, `scenes`)
- query lights/rooms/scenes
- light actions (`on`, `off`, `set`, `toggle`)

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
crestron-cli query lights [--refresh] [--json|--yaml]
crestron-cli query rooms [--refresh] [--json|--yaml]
crestron-cli query scenes [--refresh] [--json|--yaml]
crestron-cli <target> on [--json|--yaml]
crestron-cli <target> off [--json|--yaml]
crestron-cli <target> set <level> [--json|--yaml]
crestron-cli <target> toggle [--json|--yaml]
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

- default: human-readable text
- `--json`: structured JSON
- `--yaml`: structured YAML

If `OPENCLAW_PY` is present and no format flag is provided, output defaults to YAML.
