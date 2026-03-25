from __future__ import annotations

import csv
from decimal import Decimal, ROUND_HALF_UP
import io
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


def percent_to_raw(level_percent: int | float) -> int:
    bounded = max(0.0, min(100.0, float(level_percent)))
    scaled = (Decimal(str(bounded)) * Decimal("65535")) / Decimal("100")
    return int(scaled.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def raw_to_percent(level_raw: Any) -> float | None:
    try:
        value = float(level_raw)
    except Exception:
        return None
    bounded = max(0.0, min(65535.0, value))
    scaled = (Decimal(str(bounded)) * Decimal("100")) / Decimal("65535")
    rounded = scaled.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    return float(rounded)


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


def render_table(headers: list[str], rows: list[list[Any]]) -> str:
    string_rows: list[list[str]] = []
    for row in rows:
        string_rows.append(["" if value is None else str(value) for value in row])

    widths: list[int] = []
    for idx, header in enumerate(headers):
        max_row_width = 0
        for row in string_rows:
            if idx < len(row):
                max_row_width = max(max_row_width, len(row[idx]))
        widths.append(max(len(header), max_row_width))

    def _format_line(values: list[str]) -> str:
        padded: list[str] = []
        for idx, value in enumerate(values):
            padded.append(value.ljust(widths[idx]))
        return " | ".join(padded)

    header_line = _format_line(headers)
    separator = "-+-".join("-" * width for width in widths)
    body = [_format_line(row) for row in string_rows]
    return "\n".join([header_line, separator, *body])


def render_csv(headers: list[str], rows: list[list[Any]]) -> str:
    output = io.StringIO()
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(headers)
    for row in rows:
        writer.writerow(["" if value is None else value for value in row])
    return output.getvalue().rstrip("\n")


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
