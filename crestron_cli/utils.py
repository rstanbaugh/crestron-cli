from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Dict

try:
    import yaml
except Exception:
    yaml = None


def normalize_name(name: Any) -> str:
    if name is None:
        return ""
    return " ".join(str(name).strip().lower().split())


def percent_to_raw(level_percent: int) -> int:
    bounded = max(0, min(100, int(level_percent)))
    return int(round((bounded / 100.0) * 65535))


def raw_to_percent(level_raw: Any) -> float | None:
    try:
        value = float(level_raw)
    except Exception:
        return None
    bounded = max(0.0, min(65535.0, value))
    return round((bounded / 65535.0) * 100.0, 1)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def default_output_format(json_flag: bool, yaml_flag: bool) -> str:
    if json_flag:
        return "json"
    if yaml_flag:
        return "yaml"
    if os.getenv("OPENCLAW_PY"):
        return "yaml"
    return "human"


def emit_payload(payload: Dict[str, Any], fmt: str) -> None:
    if fmt == "json":
        print(json.dumps(payload, indent=2, sort_keys=False))
        return
    if fmt == "yaml":
        if yaml is None:
            raise RuntimeError("missing dependency: install pyyaml")
        print(yaml.safe_dump(payload, sort_keys=False).rstrip())
        return

    if payload.get("success") is False:
        message = str(payload.get("error") or "operation failed")
        details = payload.get("details")
        if details:
            print(f"{message}: {details}")
        else:
            print(message)
        return

    message = payload.get("message")
    if message:
        print(str(message))
        return

    data = payload.get("data")
    if isinstance(data, str):
        print(data)
    elif data is not None:
        if yaml is None:
            raise RuntimeError("missing dependency: install pyyaml")
        print(yaml.safe_dump(data, sort_keys=False).rstrip())
