# crestron-cli

Lightweight CLI for interacting with a Crestron Home server, with MVP support for:
- inventory initialization (`rooms`, `lights`, `scenes`)
- querying lights/scenes
- controlling lights by id or name

## Requirements

- Python 3.9+
- `requests`
- `pyyaml`

Environment variables:

- `CRESTRON_HOME_IP` (required)
- `CRESTRON_AUTH_TOKEN` (required)
- `CRESTRON_TIMEOUT_S` (optional, default `10`)

## Installation

From this folder:

```bash
pip install -e .
```

Or run directly:

```bash
python crestron-cli.py --help
```

## Commands

```text
crestron-cli initialize [--force] [--verbose] [--json|--yaml]
crestron-cli query lights [--refresh] [--json|--yaml]
crestron-cli query scenes [--refresh] [--json|--yaml]
crestron-cli <target> on [--json|--yaml]
crestron-cli <target> off [--json|--yaml]
crestron-cli <target> set <level> [--json|--yaml]
crestron-cli <target> toggle [--json|--yaml]
```

`<target>` may be a numeric light id or a case-insensitive cached light name.

## State cache

State is stored at:

```text
~/.openclaw/tools/crestron/state.yaml
```

`initialize` always refreshes from the server and rebuilds state maps.

## Output modes

- Default: human-readable text
- `--json`: structured JSON
- `--yaml`: structured YAML

If `OPENCLAW_PY` is present and no format flag is provided, output defaults to YAML.
