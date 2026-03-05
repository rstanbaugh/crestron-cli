from __future__ import annotations

import argparse
import sys
from typing import Any, Dict, List, Tuple

from .api import CrestronApiError, CrestronClient
from .config import ConfigError, load_config
from .state import (
    StateError,
    build_state,
    has_cached_inventory,
    list_lights,
    list_rooms,
    list_scenes,
    load_state,
    resolve_light_target,
    save_state,
    update_light_level,
)
from .utils import default_output_format, emit_payload, percent_to_raw


def _emit_error(message: str, *, fmt: str = "human", details: str | None = None) -> int:
    payload: Dict[str, Any] = {"success": False, "error": message}
    if details:
        payload["details"] = details

    if fmt in ("json", "yaml"):
        emit_payload(payload, fmt)
    else:
        if details:
            sys.stderr.write(f"error: {message}: {details}\n")
        else:
            sys.stderr.write(f"error: {message}\n")
    return 1


def _refresh_inventory(client: CrestronClient, current_state: Dict[str, Any]) -> Dict[str, Any]:
    rooms = client.get_rooms()
    lights = client.get_lights()
    scenes = client.get_scenes()

    refreshed_state = build_state(
        base_url=client.config.base_url,
        authkey=client.authkey,
        rooms=rooms,
        lights=lights,
        scenes=scenes,
        previous_state=current_state,
    )
    save_state(refreshed_state)
    return refreshed_state


def _initialize_command(argv: List[str]) -> int:
    parser = argparse.ArgumentParser(prog="crestron-cli initialize", add_help=True)
    parser.add_argument("--force", action="store_true", help="Force refresh (initialize already refreshes)")
    parser.add_argument("--verbose", action="store_true", help="Print additional progress details")
    parser.add_argument("--json", action="store_true", help="Emit structured JSON")
    parser.add_argument("--yaml", action="store_true", help="Emit structured YAML")
    args = parser.parse_args(argv)

    if args.json and args.yaml:
        return _emit_error("choose only one of --json or --yaml")

    fmt = default_output_format(args.json, args.yaml)

    try:
        config = load_config()
        client = CrestronClient(config)
        prior_state = load_state()

        state = _refresh_inventory(client, prior_state)

        rooms_count = len(((state.get("rooms") or {}).get("by_id") or {}))
        lights_count = len(((state.get("lights") or {}).get("by_id") or {}))
        scenes_count = len(((state.get("scenes") or {}).get("by_id") or {}))

        payload = {
            "success": True,
            "message": f"Initialized cache: {rooms_count} rooms, {lights_count} lights, {scenes_count} scenes",
            "data": {
                "rooms": rooms_count,
                "lights": lights_count,
                "scenes": scenes_count,
                "state_path": "~/.openclaw/tools/crestron/state.yaml",
            },
        }

        if args.verbose:
            payload["data"]["base_url"] = config.base_url

        emit_payload(payload, fmt)
        return 0
    except ConfigError as exc:
        return _emit_error(str(exc), fmt=fmt)
    except (CrestronApiError, StateError, RuntimeError) as exc:
        return _emit_error("initialize failed", fmt=fmt, details=str(exc))


def _query_command(argv: List[str]) -> int:
    parser = argparse.ArgumentParser(prog="crestron-cli query", add_help=True)
    parser.add_argument("entity", choices=["lights", "rooms", "scenes"])
    parser.add_argument("--refresh", action="store_true", help="Force refresh before query")
    parser.add_argument("--json", action="store_true", help="Emit structured JSON")
    parser.add_argument("--yaml", action="store_true", help="Emit structured YAML")
    args = parser.parse_args(argv)

    if args.json and args.yaml:
        return _emit_error("choose only one of --json or --yaml")

    fmt = default_output_format(args.json, args.yaml)

    try:
        config = load_config()
        client = CrestronClient(config)
        state = load_state()

        refreshed = False
        if args.refresh or not has_cached_inventory(state):
            state = _refresh_inventory(client, state)
            refreshed = True

        if args.entity == "lights":
            items = list_lights(state)
            if fmt == "human":
                lines = [f"Lights ({len(items)}):"]
                for row in items:
                    room_part = ""
                    if row.get("room_name"):
                        room_part = f", room {row['room_name']} ({row.get('room_id')})"
                    elif row.get("room_id") is not None:
                        room_part = f", room {row.get('room_id')}"

                    level = row.get("current_level")
                    percent = row.get("percent")
                    if level is not None and percent is not None:
                        level_part = f"level {level} ({percent}%)"
                    elif level is not None:
                        level_part = f"level {level}"
                    else:
                        level_part = "level unknown"

                    subtype = row.get("subtype")
                    subtype_part = f", subtype {subtype}" if subtype else ""
                    lines.append(
                        f"- {row.get('name')} [id {row.get('id')}] {level_part}{room_part}{subtype_part}"
                    )
                emit_payload({"success": True, "data": "\n".join(lines)}, fmt)
                return 0

            emit_payload(
                {
                    "success": True,
                    "entity": "lights",
                    "count": len(items),
                    "refreshed": refreshed,
                    "items": items,
                },
                fmt,
            )
            return 0

        if args.entity == "rooms":
            items = list_rooms(state)
            if fmt == "human":
                lines = [f"Rooms ({len(items)}):"]
                for row in items:
                    lines.append(f"- {row.get('name')} [id {row.get('id')}]")
                emit_payload({"success": True, "data": "\n".join(lines)}, fmt)
                return 0

            emit_payload(
                {
                    "success": True,
                    "entity": "rooms",
                    "count": len(items),
                    "refreshed": refreshed,
                    "items": items,
                },
                fmt,
            )
            return 0

        items = list_scenes(state)
        if fmt == "human":
            lines = [f"Scenes ({len(items)}):"]
            for row in items:
                room_part = ""
                if row.get("room_name"):
                    room_part = f", room {row['room_name']} ({row.get('room_id')})"
                elif row.get("room_id") is not None:
                    room_part = f", room {row.get('room_id')}"
                lines.append(f"- {row.get('name')} [id {row.get('id')}]" + room_part)
            emit_payload({"success": True, "data": "\n".join(lines)}, fmt)
            return 0

        emit_payload(
            {
                "success": True,
                "entity": "scenes",
                "count": len(items),
                "refreshed": refreshed,
                "items": items,
            },
            fmt,
        )
        return 0
    except ConfigError as exc:
        return _emit_error(str(exc), fmt=fmt)
    except (CrestronApiError, StateError, RuntimeError) as exc:
        return _emit_error("query failed", fmt=fmt, details=str(exc))


def _parse_action_level(action: str, level_text: str | None) -> Tuple[int | None, str | None]:
    if action != "set":
        if level_text is not None:
            return None, "unexpected level argument"
        return None, None

    if level_text is None:
        return None, "set requires level argument (0-100)"

    try:
        level_value = int(level_text)
    except Exception:
        return None, "level must be an integer between 0 and 100"

    if level_value < 0 or level_value > 100:
        return None, "level must be between 0 and 100"

    return level_value, None


def _normalize_target_token(target: str) -> str:
    token = target.strip()
    lowered = token.lower()
    for prefix in ("light=", "id=", "light:", "id:"):
        if lowered.startswith(prefix):
            value = token[len(prefix):].strip()
            if value:
                return value
    return token


def _action_command(argv: List[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="crestron-cli <target>",
        add_help=True,
        usage="crestron-cli <target> {on|off|set|toggle} [level] [--json|--yaml]",
    )
    parser.add_argument("target")
    parser.add_argument("action", choices=["on", "off", "set", "toggle"])
    parser.add_argument("level", nargs="?")
    parser.add_argument("--json", action="store_true", help="Emit structured JSON")
    parser.add_argument("--yaml", action="store_true", help="Emit structured YAML")
    args = parser.parse_args(argv)

    if args.json and args.yaml:
        return _emit_error("choose only one of --json or --yaml")

    fmt = default_output_format(args.json, args.yaml)

    level_percent, parse_error = _parse_action_level(args.action, args.level)
    if parse_error:
        return _emit_error(parse_error, fmt=fmt)

    try:
        config = load_config()
        client = CrestronClient(config)
        state = load_state()

        if not has_cached_inventory(state):
            state = _refresh_inventory(client, state)

        target_token = _normalize_target_token(args.target)
        light_id, light = resolve_light_target(state, target_token)
        current_level = light.get("current_level")
        if current_level is None:
            current_level = 0

        if args.action == "on":
            target_raw = 65535
            action_desc = "turned on"
            target_percent = 100
        elif args.action == "off":
            target_raw = 0
            action_desc = "turned off"
            target_percent = 0
        elif args.action == "toggle":
            target_raw = 0 if int(current_level) > 0 else 65535
            action_desc = "toggled"
            target_percent = 0 if target_raw == 0 else 100
        else:
            assert level_percent is not None
            target_raw = percent_to_raw(level_percent)
            action_desc = f"set to {level_percent}%"
            target_percent = level_percent

        try:
            client.set_light_state(light_id=light_id, level_raw=target_raw)
        except CrestronApiError as exc:
            if exc.error_source in (5001, 5002):
                state = _refresh_inventory(client, state)
                client.set_light_state(light_id=light_id, level_raw=target_raw)
            else:
                raise

        state = update_light_level(state, light_id=light_id, level_raw=target_raw)
        save_state(state)

        light_name = str(light.get("name") or f"Light {light_id}")
        payload = {
            "success": True,
            "message": f"{light_name} {action_desc}",
            "data": {
                "id": light_id,
                "name": light_name,
                "action": args.action,
                "level_raw": target_raw,
                "level_percent": target_percent,
            },
        }
        emit_payload(payload, fmt)
        return 0

    except ConfigError as exc:
        return _emit_error(str(exc), fmt=fmt)
    except (CrestronApiError, StateError, RuntimeError) as exc:
        return _emit_error("action failed", fmt=fmt, details=str(exc))


def _print_root_help() -> None:
    text = "\n".join(
        [
            "crestron-cli",
            "",
            "Usage:",
            "  crestron-cli initialize [--force] [--verbose] [--json|--yaml]",
            "  crestron-cli query lights [--refresh] [--json|--yaml]",
            "  crestron-cli query rooms [--refresh] [--json|--yaml]",
            "  crestron-cli query scenes [--refresh] [--json|--yaml]",
            "  crestron-cli <target> on [--json|--yaml]",
            "  crestron-cli <target> off [--json|--yaml]",
            "  crestron-cli <target> set <level> [--json|--yaml]",
            "  crestron-cli <target> toggle [--json|--yaml]",
            "",
            "Environment:",
            "  CRESTRON_HOME_IP (required)",
            "  CRESTRON_AUTH_TOKEN (required)",
            "  CRESTRON_TIMEOUT_S (optional, default 10)",
        ]
    )
    print(text)


def main(argv: List[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    if not argv or argv[0] in {"-h", "--help", "help"}:
        _print_root_help()
        return 0

    command = argv[0]
    if command == "initialize":
        return _initialize_command(argv[1:])
    if command == "query":
        return _query_command(argv[1:])

    return _action_command(argv)


def cli() -> None:
    raise SystemExit(main())


if __name__ == "__main__":
    raise SystemExit(main())
