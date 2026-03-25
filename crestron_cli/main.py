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
    resolve_scene_target,
    save_state,
    update_light_level,
)
from .utils import default_output_format, emit_payload, percent_to_raw, render_csv, render_table


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


def _query_output_format(*, json_flag: bool, yaml_flag: bool, raw_flag: bool) -> str:
    if json_flag:
        return "json"
    if yaml_flag:
        return "yaml"
    if raw_flag:
        return "raw"
    return "human"


def _parse_room_filter_token(token: str) -> Tuple[int | None, str | None]:
    lowered_token = token.lower().strip()
    room_value: str | None = None
    for prefix in ("room=", "room:"):
        if lowered_token.startswith(prefix):
            room_value = token[len(prefix):].strip()
            break

    if room_value is None:
        return None, "query filter must use room=<id>"
    if not room_value or not room_value.isdigit():
        return None, "room filter requires a numeric id"
    return int(room_value), None


def _parse_query_selector(selector: str | None, query_filter: str | None) -> Tuple[str, int | None, str | None]:
    tokens: List[str] = []
    for value in (selector, query_filter):
        token = (value or "").strip()
        if token:
            tokens.append(token)

    entity: str | None = None
    room_id: int | None = None

    for token in tokens:
        normalized = token.lower()
        if normalized in {"lights", "rooms", "scenes"}:
            if entity is not None:
                return "", None, "multiple entities provided; use only one of lights, rooms, or scenes"
            entity = normalized
            continue

        parsed_room_id, room_error = _parse_room_filter_token(token)
        if room_error:
            return "", None, room_error
        if room_id is not None:
            return "", None, "multiple room filters provided; use only one room=<id>"
        room_id = parsed_room_id

    if entity is None:
        entity = "lights"

    if room_id is not None and entity not in {"lights", "scenes"}:
        return "", None, "room filter is only supported for lights and scenes queries"

    return entity, room_id, None


def _format_percent(value: Any) -> str:
    try:
        return f"{float(value):.1f}"
    except Exception:
        return ""


def _emit_query_table(entity: str, items: List[Dict[str, Any]], room_id: int | None) -> None:
    if entity == "lights":
        if room_id is None:
            headers = ["Room", "Room ID", "Name", "Light ID", "Current Level", "Percent", "Subtype"]
            rows = [
                [
                    row.get("room_name"),
                    row.get("room_id"),
                    row.get("name"),
                    row.get("id"),
                    row.get("current_level"),
                    _format_percent(row.get("percent")),
                    row.get("subtype"),
                ]
                for row in items
            ]
            title = f"Lights ({len(items)})"
        else:
            headers = ["Room", "Room ID", "Name", "Light ID", "Current Level", "Percent", "Subtype"]
            rows = [
                [
                    row.get("room_name"),
                    row.get("room_id"),
                    row.get("name"),
                    row.get("id"),
                    row.get("current_level"),
                    _format_percent(row.get("percent")),
                    row.get("subtype"),
                ]
                for row in items
            ]
            title = f"Lights in room {room_id} ({len(items)})"
        print(f"{title}\n{render_table(headers, rows)}")
        return

    if entity == "rooms":
        headers = ["Name", "Room ID"]
        rows = [[row.get("name"), row.get("id")] for row in items]
        print(f"Rooms ({len(items)})\n{render_table(headers, rows)}")
        return

    headers = ["Room", "Room ID", "Name", "Scene Type", "Scene ID"]
    rows = [
        [
            row.get("room_name"),
            row.get("room_id"),
            row.get("name"),
            row.get("scene_type"),
            row.get("id"),
        ]
        for row in items
    ]
    print(f"Scenes ({len(items)})\n{render_table(headers, rows)}")


def _emit_query_raw(entity: str, items: List[Dict[str, Any]], room_id: int | None) -> None:
    if entity == "lights":
        if room_id is None:
            headers = ["Room", "Room ID", "Name", "Light ID", "Current Level", "Percent", "Subtype"]
            rows = [
                [
                    row.get("room_name"),
                    row.get("room_id"),
                    row.get("name"),
                    row.get("id"),
                    row.get("current_level"),
                    _format_percent(row.get("percent")),
                    row.get("subtype"),
                ]
                for row in items
            ]
        else:
            headers = ["Room", "Room ID", "Name", "Light ID", "Current Level", "Percent", "Subtype"]
            rows = [
                [
                    row.get("room_name"),
                    row.get("room_id"),
                    row.get("name"),
                    row.get("id"),
                    row.get("current_level"),
                    _format_percent(row.get("percent")),
                    row.get("subtype"),
                ]
                for row in items
            ]
        print(render_csv(headers, rows))
        return

    if entity == "rooms":
        headers = ["Name", "Room ID"]
        rows = [[row.get("name"), row.get("id")] for row in items]
        print(render_csv(headers, rows))
        return

    headers = ["Room", "Room ID", "Name", "Scene Type", "Scene ID"]
    rows = [
        [
            row.get("room_name"),
            row.get("room_id"),
            row.get("name"),
            row.get("scene_type"),
            row.get("id"),
        ]
        for row in items
    ]
    print(render_csv(headers, rows))


def _reorder_item_keys(item: Dict[str, Any], preferred_keys: List[str]) -> Dict[str, Any]:
    ordered: Dict[str, Any] = {}
    for key in preferred_keys:
        if key in item:
            ordered[key] = item.get(key)
    for key, value in item.items():
        if key not in ordered:
            ordered[key] = value
    return ordered


def _ordered_query_items(entity: str, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if entity == "lights":
        preferred = ["room_name", "room_id", "name", "id", "current_level", "percent", "subtype"]
    elif entity == "rooms":
        preferred = ["name", "id"]
    else:
        preferred = ["room_name", "room_id", "name", "scene_type", "id", "status"]

    return [_reorder_item_keys(item, preferred) for item in items]


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
    parser.add_argument("token1", nargs="?", help="Entity or filter: lights|rooms|scenes or room=<id>")
    parser.add_argument("token2", nargs="?", help="Optional second token in either order")
    parser.add_argument("--refresh", action="store_true", help="Force refresh before query")
    parser.add_argument("--raw", action="store_true", help="Emit comma-separated values (CSV)")
    parser.add_argument("--json", action="store_true", help="Emit structured JSON")
    parser.add_argument("--yaml", action="store_true", help="Emit structured YAML")
    args = parser.parse_args(argv)

    if sum(1 for flag in (args.raw, args.json, args.yaml) if flag) > 1:
        return _emit_error("choose only one of --raw, --json, or --yaml")

    fmt = _query_output_format(json_flag=args.json, yaml_flag=args.yaml, raw_flag=args.raw)
    entity, room_id, parse_error = _parse_query_selector(args.token1, args.token2)
    if parse_error:
        error_fmt = "human" if fmt == "raw" else fmt
        return _emit_error(parse_error, fmt=error_fmt)

    try:
        config = load_config()
        client = CrestronClient(config)
        state = load_state()

        refreshed = False
        if args.refresh or not has_cached_inventory(state):
            state = _refresh_inventory(client, state)
            refreshed = True

        if entity == "lights":
            items = list_lights(state, room_id=room_id)
            if fmt == "human":
                _emit_query_table(entity, items, room_id)
                return 0
            if fmt == "raw":
                _emit_query_raw(entity, items, room_id)
                return 0

            items = _ordered_query_items(entity, items)

            emit_payload(
                {
                    "success": True,
                    "entity": "lights",
                    "count": len(items),
                    "refreshed": refreshed,
                    "room_id": room_id,
                    "items": items,
                },
                fmt,
            )
            return 0

        if entity == "rooms":
            items = list_rooms(state)
            if fmt == "human":
                _emit_query_table(entity, items, room_id)
                return 0
            if fmt == "raw":
                _emit_query_raw(entity, items, room_id)
                return 0

            items = _ordered_query_items(entity, items)

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

        items = list_scenes(state, room_id=room_id)

        # Older cache files may not have scene_type populated; auto-refresh once.
        missing_scene_type = any(not row.get("scene_type") for row in items)
        if missing_scene_type and not args.refresh:
            state = _refresh_inventory(client, state)
            refreshed = True
            items = list_scenes(state, room_id=room_id)

        if fmt == "human":
            _emit_query_table(entity, items, room_id)
            return 0
        if fmt == "raw":
            _emit_query_raw(entity, items, room_id)
            return 0

        items = _ordered_query_items(entity, items)

        emit_payload(
            {
                "success": True,
                "entity": "scenes",
                "count": len(items),
                "refreshed": refreshed,
                "room_id": room_id,
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


def _normalize_scene_target_token(target: str) -> str:
    token = target.strip()
    lowered = token.lower()
    for prefix in ("scene=", "id=", "scene:", "id:"):
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


def _scene_command(argv: List[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="crestron-cli scene",
        add_help=True,
        usage="crestron-cli scene <target> activate [--type <lighting|media>] [--room-id <id>] [--json|--yaml]",
    )
    parser.add_argument("target")
    parser.add_argument("action", choices=["activate"])
    parser.add_argument("--type", choices=["lighting", "media"], dest="scene_type", help="Optional scene type")
    parser.add_argument("--room-id", type=int, help="Optional room id for disambiguation")
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

        if not has_cached_inventory(state):
            state = _refresh_inventory(client, state)

        target_token = _normalize_scene_target_token(args.target)
        scene_id, scene = resolve_scene_target(
            state,
            target_token,
            scene_type=args.scene_type,
            room_id=args.room_id,
        )

        try:
            client.recall_scene(scene_id=scene_id)
        except CrestronApiError as exc:
            if exc.error_source in (5001, 5002):
                state = _refresh_inventory(client, state)
                client.recall_scene(scene_id=scene_id)
            else:
                raise

        scene_name = str(scene.get("name") or f"Scene {scene_id}")
        payload = {
            "success": True,
            "message": f"Scene {scene_name} activated",
            "data": {
                "id": scene_id,
                "name": scene_name,
                "action": "activate",
                "scene_type": scene.get("scene_type"),
                "room_id": scene.get("room_id"),
            },
        }
        emit_payload(payload, fmt)
        return 0

    except ConfigError as exc:
        return _emit_error(str(exc), fmt=fmt)
    except (CrestronApiError, StateError, RuntimeError) as exc:
        return _emit_error("scene action failed", fmt=fmt, details=str(exc))


def _print_root_help() -> None:
    text = "\n".join(
        [
            "crestron-cli",
            "",
            "Usage:",
            "  crestron-cli initialize [--force] [--verbose] [--json|--yaml]",
            "  crestron-cli query [lights|scenes] [room=<id>] [--refresh] [--raw|--json|--yaml]",
            "  crestron-cli query room=<id> [lights|scenes] [--refresh] [--raw|--json|--yaml]",
            "  crestron-cli query rooms [--refresh] [--raw|--json|--yaml]",
            "  crestron-cli scene <target> activate [--type <lighting|media>] [--room-id <id>] [--json|--yaml]",
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
    if command == "scene":
        return _scene_command(argv[1:])

    return _action_command(argv)


def cli() -> None:
    raise SystemExit(main())


if __name__ == "__main__":
    raise SystemExit(main())
