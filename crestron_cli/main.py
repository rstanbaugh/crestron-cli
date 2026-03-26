from __future__ import annotations

import argparse
import sys
from typing import Any, Dict, List, Tuple

from .api import CrestronApiError, CrestronClient
from .config import ConfigError, load_config
from .state import (
    StateError,
    build_state,
    get_speaker_player_default,
    has_cached_inventory,
    list_lights,
    list_rooms,
    list_scenes,
    list_speakers,
    load_state,
    resolve_light_target,
    resolve_room_target,
    resolve_scene_target,
    resolve_speaker_source_target,
    resolve_speaker_target,
    save_state,
    set_speaker_player_default,
    update_light_level,
    update_speaker_state,
)
from .utils import default_output_format, emit_payload, percent_to_raw, raw_to_percent, render_csv, render_table


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
    speakers = client.get_speakers()

    refreshed_state = build_state(
        base_url=client.config.base_url,
        authkey=client.authkey,
        rooms=rooms,
        lights=lights,
        scenes=scenes,
        speakers=speakers,
        previous_state=current_state,
    )

    # Preserve global audio defaults across refresh.
    prior_defaults = current_state.get("audio_defaults") if isinstance(current_state, dict) else None
    if isinstance(prior_defaults, dict):
        refreshed_state["audio_defaults"] = {
            "A": dict((prior_defaults.get("A") or {})) if isinstance(prior_defaults.get("A"), dict) else {},
            "B": dict((prior_defaults.get("B") or {})) if isinstance(prior_defaults.get("B"), dict) else {},
        }

    # Seed missing global audio defaults from currently active room sources.
    defaults = _get_audio_defaults(refreshed_state)
    for speaker in list_speakers(refreshed_state):
        current_source_id = speaker.get("current_source_id")
        if current_source_id is None:
            continue
        source_name = None
        for source in speaker.get("available_sources") or []:
            if not isinstance(source, dict):
                continue
            try:
                sid = int(source.get("id")) if source.get("id") is not None else None
            except Exception:
                sid = None
            if sid is not None and int(current_source_id) == sid:
                source_name = str(source.get("source_name") or "")
                break
        player = _infer_player_from_source_name(source_name)
        if player in {"A", "B"}:
            existing = defaults.get(player) or {}
            if existing.get("service_id") is None:
                refreshed_state = _set_audio_default(refreshed_state, player, int(current_source_id), _strip_player_prefix(source_name) or str(source_name or ""))

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


def _parse_room_filter_token(token: str) -> Tuple[str | None, str | None]:
    lowered_token = token.lower().strip()
    room_value: str | None = None
    for prefix in ("room=", "room:"):
        if lowered_token.startswith(prefix):
            room_value = token[len(prefix):].strip()
            break

    if room_value is None:
        return None, "query filter must use room=<id|name>"
    if not room_value:
        return None, "room filter requires a room id or name"
    return room_value, None


def _parse_query_selector(selector: str | None, query_filter: str | None, query_extra: str | None) -> Tuple[str, str | None, str | None, str | None]:
    tokens: List[str] = []
    for value in (selector, query_filter, query_extra):
        token = (value or "").strip()
        if token:
            tokens.append(token)

    entity: str | None = None
    room_selector: str | None = None
    audio_view: str | None = None

    for token in tokens:
        normalized = token.lower()
        if normalized in {"lights", "rooms", "scenes", "audio", "speakers"}:
            normalized_entity = "audio" if normalized == "speakers" else normalized
            if entity is not None:
                return "", None, None, "multiple entities provided; use only one of lights, rooms, scenes, or audio"
            entity = normalized_entity
            continue

        if normalized in {"player", "players"}:
            if audio_view is not None:
                return "", None, None, "multiple audio selectors provided; use only one of player or service"
            audio_view = "player"
            continue

        if normalized in {"service", "services"}:
            if audio_view is not None:
                return "", None, None, "multiple audio selectors provided; use only one of player or service"
            audio_view = "service"
            continue

        if normalized in {"source", "sources"}:
            return "", None, None, "audio view 'source' was renamed to 'service'"
            continue

        parsed_room_selector, room_error = _parse_room_filter_token(token)
        if room_error:
            return "", None, None, room_error
        if room_selector is not None:
            return "", None, None, "multiple room filters provided; use only one room=<id|name>"
        room_selector = parsed_room_selector

    if entity is None:
        entity = "lights"

    if audio_view is not None and entity != "audio":
        return "", None, None, "audio player/service selectors are only valid with query audio"

    if room_selector is not None and entity not in {"lights", "scenes", "audio"}:
        return "", None, None, "room filter is only supported for lights, scenes, and audio queries"

    if audio_view is None:
        audio_view = "status"

    return entity, room_selector, audio_view, None


def _print_query_help(entity: str | None, audio_view: str | None = None) -> None:
    if entity == "audio":
        view_hint = ""
        if audio_view == "player":
            view_hint = "\nSelected view: player"
        elif audio_view == "service":
            view_hint = "\nSelected view: service"
        text = "\n".join(
            [
                "crestron-cli query audio",
                "",
                "Usage:",
                "  crestron-cli query audio [<view>] [room=<id|name>] [--refresh] [--raw|--json|--yaml]",
                "  crestron-cli query audio player [--refresh] [--raw|--json|--yaml]",
                "  crestron-cli query audio service [room=<id|name>] [--refresh] [--raw|--json|--yaml]",
                "  crestron-cli query room=<id|name> audio [player|service] [--refresh] [--raw|--json|--yaml]",
                "",
                "Views:",
                "  status (default)  Room audio status (name, power, mute, volume %, player)",
                "  audio player   Global Player A/B service mapping",
                "  audio service  Available service names and service IDs",
                "",
                "Options:",
                "  --refresh  Bypass cache and pull fresh inventory from controller before query",
                "  Output mode: blank (table) | --raw (CSV) | --json | --yaml",
                view_hint,
            ]
        ).rstrip()
        print(text)
        return

    if entity == "lights":
        print(
            "\n".join(
                [
                    "crestron-cli query lights",
                    "",
                    "Usage:",
                    "  crestron-cli query lights [room=<id|name>] [--refresh] [--raw|--json|--yaml]",
                    "  crestron-cli query room=<id|name> lights [--refresh] [--raw|--json|--yaml]",
                ]
            )
        )
        return

    if entity == "scenes":
        print(
            "\n".join(
                [
                    "crestron-cli query scenes",
                    "",
                    "Usage:",
                    "  crestron-cli query scenes [room=<id|name>] [--refresh] [--raw|--json|--yaml]",
                    "  crestron-cli query room=<id|name> scenes [--refresh] [--raw|--json|--yaml]",
                ]
            )
        )
        return

    if entity == "rooms":
        print(
            "\n".join(
                [
                    "crestron-cli query rooms",
                    "",
                    "Usage:",
                    "  crestron-cli query rooms [--refresh] [--raw|--json|--yaml]",
                ]
            )
        )
        return


def _infer_player_from_source_name(source_name: str | None) -> str:
    lowered = str(source_name or "").lower().strip()
    if lowered.startswith("player a"):
        return "A"
    if lowered.startswith("player b"):
        return "B"
    return "unset"


def _strip_player_prefix(source_name: str | None) -> str | None:
    text = str(source_name or "").strip()
    lowered = text.lower()
    for prefix in ("player a ", "player b "):
        if lowered.startswith(prefix):
            return text[len(prefix):].strip()
    return text or None


def _get_audio_defaults(state: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    defaults = state.get("audio_defaults")
    if not isinstance(defaults, dict):
        defaults = {"A": {}, "B": {}}
        state["audio_defaults"] = defaults
    for player in ("A", "B"):
        entry = defaults.get(player)
        if not isinstance(entry, dict):
            defaults[player] = {}
    return defaults


def _set_audio_default(state: Dict[str, Any], player: str, service_id: int, service_name: str) -> Dict[str, Any]:
    defaults = _get_audio_defaults(state)
    defaults[player] = {
        "service_id": int(service_id),
        "service_name": str(service_name),
    }
    return state


def _player_source_catalog(state: Dict[str, Any]) -> Dict[str, Dict[int, str]]:
    catalog: Dict[str, Dict[int, str]] = {"A": {}, "B": {}}
    for speaker in list_speakers(state):
        for source in speaker.get("available_sources") or []:
            if not isinstance(source, dict):
                continue
            try:
                source_id = int(source.get("id")) if source.get("id") is not None else None
            except Exception:
                source_id = None
            if source_id is None:
                continue
            raw_name = str(source.get("source_name") or "")
            player = _infer_player_from_source_name(raw_name)
            if player in {"A", "B"}:
                catalog[player][source_id] = _strip_player_prefix(raw_name) or raw_name
    return catalog


def _collect_audio_services(state: Dict[str, Any], room_id: int | None = None) -> List[Dict[str, Any]]:
    by_id: Dict[int, Dict[str, Any]] = {}
    for speaker in list_speakers(state, room_id=room_id):
        for source in speaker.get("available_sources") or []:
            if not isinstance(source, dict):
                continue
            try:
                source_id = int(source.get("id")) if source.get("id") is not None else None
            except Exception:
                source_id = None
            if source_id is None:
                continue
            raw_source_name = str(source.get("source_name") or "")
            player = _infer_player_from_source_name(raw_source_name)
            source_name = _strip_player_prefix(raw_source_name) or raw_source_name
            by_id[source_id] = {
                "service_id": source_id,
                "service_name": source_name,
                "player": f"Player {player}" if player in {"A", "B"} else "unset",
            }
    items = list(by_id.values())
    items.sort(
        key=lambda row: (
            str(row.get("player") or "").lower(),
            str(row.get("service_name") or "").lower(),
            int(row.get("service_id") or 0),
        )
    )
    return items


def _list_audio_status(state: Dict[str, Any], room_id: int | None = None) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for speaker in list_speakers(state, room_id=room_id):
        volume_percent = speaker.get("current_volume_percent")
        try:
            volume_percent = int(round(float(volume_percent))) if volume_percent is not None else None
            if volume_percent is not None and volume_percent > 100:
                normalized = raw_to_percent(volume_percent)
                volume_percent = int(round(normalized)) if normalized is not None else None
        except Exception:
            volume_percent = None

        current_source_name: str | None = None
        current_source_id = speaker.get("current_source_id")
        for source in speaker.get("available_sources") or []:
            if not isinstance(source, dict):
                continue
            try:
                source_id = int(source.get("id")) if source.get("id") is not None else None
            except Exception:
                source_id = None
            if source_id is not None and current_source_id is not None and int(current_source_id) == source_id:
                current_source_name = str(source.get("source_name") or "")
                break

        player = _infer_player_from_source_name(current_source_name)
        service_display = _strip_player_prefix(current_source_name) if player in {"A", "B"} else None

        items.append(
            {
                "name": speaker.get("name"),
                "id": speaker.get("id"),
                "current_power_state": speaker.get("current_power_state") or "unknown",
                "current_mute_state": speaker.get("current_mute_state") or "unknown",
                "current_volume_percent": volume_percent,
                "player": player,
                "service": service_display,
                "room_id": speaker.get("room_id"),
                "room_name": speaker.get("room_name"),
            }
        )
    items.sort(key=lambda row: (str(row.get("name") or "").lower(), int(row.get("id") or 0)))
    return items


def _list_audio_players(state: Dict[str, Any]) -> List[Dict[str, Any]]:
    defaults = _get_audio_defaults(state)
    items: List[Dict[str, Any]] = []
    for player in ("A", "B"):
        entry = defaults.get(player) or {}
        service_id = entry.get("service_id")
        service_name = entry.get("service_name")
        items.append(
            {
                "player": f"Player {player}",
                "service": _strip_player_prefix(str(service_name) if service_name else None) or "unset",
                "service_id": service_id,
            }
        )
    return items


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

    if entity == "audio":
        if items and "player" in items[0] and "service" in items[0] and "name" not in items[0]:
            headers = ["Player", "Service", "Service ID"]
            rows = [[row.get("player"), row.get("service"), row.get("service_id")] for row in items]
            print(f"Audio players ({len(items)})\n{render_table(headers, rows)}")
            return

        if items and "service_id" in items[0] and "service_name" in items[0] and "player" in items[0]:
            headers = ["Player", "Service", "Service ID"]
            rows = [[row.get("player"), row.get("service_name"), row.get("service_id")] for row in items]
            print(f"Audio services ({len(items)})\n{render_table(headers, rows)}")
            return

        if items and "service_id" in items[0] and "service_name" in items[0] and "player" not in items[0]:
            headers = ["Service", "Service ID"]
            rows = [[row.get("service_name"), row.get("service_id")] for row in items]
            print(f"Audio services ({len(items)})\n{render_table(headers, rows)}")
            return

        headers = ["Name", "Speaker ID", "Power", "Mute", "Volume %", "Player", "Service"]
        rows = [
            [
                row.get("name"),
                row.get("id"),
                row.get("current_power_state"),
                row.get("current_mute_state"),
                row.get("current_volume_percent"),
                row.get("player"),
                row.get("service"),
            ]
            for row in items
        ]
        title = f"Audio ({len(items)})" if room_id is None else f"Audio in room {room_id} ({len(items)})"
        print(f"{title}\n{render_table(headers, rows)}")
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

    if entity == "audio":
        if items and "player" in items[0] and "service" in items[0] and "name" not in items[0]:
            headers = ["Player", "Service", "Service ID"]
            rows = [[row.get("player"), row.get("service"), row.get("service_id")] for row in items]
            print(render_csv(headers, rows))
            return

        if items and "service_id" in items[0] and "service_name" in items[0] and "player" in items[0]:
            headers = ["Player", "Service", "Service ID"]
            rows = [[row.get("player"), row.get("service_name"), row.get("service_id")] for row in items]
            print(render_csv(headers, rows))
            return

        if items and "service_id" in items[0] and "service_name" in items[0] and "player" not in items[0]:
            headers = ["Service", "Service ID"]
            rows = [[row.get("service_name"), row.get("service_id")] for row in items]
            print(render_csv(headers, rows))
            return

        headers = ["Name", "Speaker ID", "Power", "Mute", "Volume %", "Player", "Service"]
        rows = [
            [
                row.get("name"),
                row.get("id"),
                row.get("current_power_state"),
                row.get("current_mute_state"),
                row.get("current_volume_percent"),
                row.get("player"),
                row.get("service"),
            ]
            for row in items
        ]
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
    elif entity == "audio":
        if items and "player" in items[0] and "service" in items[0]:
            preferred = ["player", "service", "service_id"]
        elif items and "service_id" in items[0] and "service_name" in items[0] and "player" not in items[0]:
            preferred = ["service_name", "service_id"]
        else:
            preferred = [
                "name",
                "id",
                "current_power_state",
                "current_mute_state",
                "current_volume_percent",
                "player",
                "service",
                "room_id",
                "room_name",
            ]
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
        speakers_count = len(((state.get("speakers") or {}).get("by_id") or {}))

        payload = {
            "success": True,
            "message": f"Initialized cache: {rooms_count} rooms, {lights_count} lights, {scenes_count} scenes, {speakers_count} speakers",
            "data": {
                "rooms": rooms_count,
                "lights": lights_count,
                "scenes": scenes_count,
                "speakers": speakers_count,
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
    if any(flag in argv for flag in ("-h", "--help")):
        positional_tokens = [token for token in argv if token not in {"-h", "--help"} and not token.startswith("--")]
        token1 = positional_tokens[0] if len(positional_tokens) > 0 else None
        token2 = positional_tokens[1] if len(positional_tokens) > 1 else None
        token3 = positional_tokens[2] if len(positional_tokens) > 2 else None
        entity, _, audio_view, _ = _parse_query_selector(token1, token2, token3)
        if entity in {"audio", "lights", "scenes", "rooms"}:
            _print_query_help(entity, audio_view)
            return 0

    parser = argparse.ArgumentParser(prog="crestron-cli query", add_help=True)
    parser.add_argument("token1", nargs="?", help="Entity or filter: lights|rooms|scenes|audio or room=<id|name>")
    parser.add_argument("token2", nargs="?", help="Optional second token in either order")
    parser.add_argument("token3", nargs="?", help="Optional audio selector: player|service")
    parser.add_argument("--refresh", action="store_true", help="Force refresh before query")
    parser.add_argument("--raw", action="store_true", help="Emit comma-separated values (CSV)")
    parser.add_argument("--json", action="store_true", help="Emit structured JSON")
    parser.add_argument("--yaml", action="store_true", help="Emit structured YAML")
    args = parser.parse_args(argv)

    if sum(1 for flag in (args.raw, args.json, args.yaml) if flag) > 1:
        return _emit_error("choose only one of --raw, --json, or --yaml")

    fmt = _query_output_format(json_flag=args.json, yaml_flag=args.yaml, raw_flag=args.raw)
    entity, room_selector, audio_view, parse_error = _parse_query_selector(args.token1, args.token2, args.token3)
    if parse_error:
        error_fmt = "human" if fmt == "raw" else fmt
        return _emit_error(parse_error, fmt=error_fmt)

    try:
        config = load_config()
        client = CrestronClient(config)
        state = load_state()

        refreshed = False
        force_live_query = True
        if args.refresh or force_live_query or not has_cached_inventory(state):
            state = _refresh_inventory(client, state)
            refreshed = True

        room_id: int | None = None
        if room_selector is not None:
            try:
                room_id = resolve_room_target(state, room_selector)
            except StateError:
                if not refreshed:
                    state = _refresh_inventory(client, state)
                    refreshed = True
                    room_id = resolve_room_target(state, room_selector)
                else:
                    raise

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

        if entity == "audio":
            if audio_view == "player":
                items = _list_audio_players(state)
            elif audio_view == "service":
                items = _collect_audio_services(state, room_id=room_id)
            else:
                items = _list_audio_status(state, room_id=room_id)
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
                    "entity": "audio",
                    "view": audio_view,
                    "count": len(items),
                    "refreshed": refreshed,
                    "room_id": room_id,
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


def _normalize_speaker_target_token(target: str) -> str:
    token = target.strip()
    lowered = token.lower()
    for prefix in ("speaker=", "id=", "speaker:", "id:", "room=", "room:"):
        if lowered.startswith(prefix):
            value = token[len(prefix):].strip()
            if value:
                return value
    return token


def _action_command(argv: List[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="crestron-cli light <target>",
        add_help=True,
        usage="crestron-cli light <target> {on|off|set|toggle} [level] [--json|--yaml]",
    )
    parser.add_argument("target")
    parser.add_argument("action", nargs="?", choices=["on", "off", "set", "toggle"])
    parser.add_argument("level", nargs="?")
    parser.add_argument("--json", action="store_true", help="Emit structured JSON")
    parser.add_argument("--yaml", action="store_true", help="Emit structured YAML")
    args = parser.parse_args(argv)

    if args.json and args.yaml:
        return _emit_error("choose only one of --json or --yaml")

    fmt = default_output_format(args.json, args.yaml)

    if args.action is None:
        return _emit_error(
            "light action is required",
            fmt=fmt,
            details="try 'crestron-cli query lights room=10 --yaml' to list room lights, then 'crestron-cli light id=<light_id> on|off|set|toggle'",
        )

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

        observed_from_refresh = True
        observed_level_raw: int | None = None
        observed_level_percent: float | None = None
        try:
            state = _refresh_inventory(client, state)
            observed_light = ((state.get("lights") or {}).get("by_id") or {}).get(str(light_id)) or {}
            if isinstance(observed_light, dict):
                try:
                    observed_level_raw = int(observed_light.get("current_level")) if observed_light.get("current_level") is not None else None
                except Exception:
                    observed_level_raw = None
                try:
                    observed_level_percent = float(observed_light.get("percent")) if observed_light.get("percent") is not None else None
                except Exception:
                    observed_level_percent = None
        except Exception:
            observed_from_refresh = False
            state = update_light_level(state, light_id=light_id, level_raw=target_raw)
            save_state(state)
            observed_level_raw = target_raw
            observed_level_percent = float(target_percent)

        light_name = str(light.get("name") or f"Light {light_id}")
        if observed_level_raw is None:
            current_state = "unknown"
        else:
            current_state = "on" if int(observed_level_raw) > 0 else "off"
        payload = {
            "success": True,
            "message": f"{light_name} {action_desc}",
            "data": {
                "id": light_id,
                "name": light_name,
                "action": args.action,
                "current_state": current_state,
                "requested_level_raw": target_raw,
                "requested_level_percent": target_percent,
                "level_raw": observed_level_raw,
                "level_percent": observed_level_percent,
                "observed_from_refresh": observed_from_refresh,
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
        usage="crestron-cli scene <target> {on|activate} [--type <lighting|media>] [--room-id <id>] [--json|--yaml]",
    )
    parser.add_argument("target")
    parser.add_argument("action", nargs="?", choices=["on", "activate"])
    parser.add_argument("--type", choices=["lighting", "media"], dest="scene_type", help="Optional scene type")
    parser.add_argument("--room-id", type=int, help="Optional room id for disambiguation")
    parser.add_argument("--json", action="store_true", help="Emit structured JSON")
    parser.add_argument("--yaml", action="store_true", help="Emit structured YAML")
    args = parser.parse_args(argv)

    if args.json and args.yaml:
        return _emit_error("choose only one of --json or --yaml")

    fmt = default_output_format(args.json, args.yaml)

    if args.action is None:
        return _emit_error(
            "scene action is required",
            fmt=fmt,
            details="try 'crestron-cli query scenes --yaml' then 'crestron-cli scene id=<scene_id> on'",
        )

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
                "action": "on" if args.action == "on" else "activate",
                "current_state": "activated",
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


def _audio_command(argv: List[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="crestron-cli audio",
        add_help=True,
        usage="crestron-cli audio <target> {on|off|set|mute|unmute|toggle|service|player} [value] [--player <A|B>] [--json|--yaml]\n       crestron-cli audio <A|B>=<service-id|service-name>",
    )
    parser.add_argument("arg1")
    parser.add_argument("arg2", nargs="?")
    parser.add_argument("arg3", nargs="?")
    parser.add_argument("--player", choices=["A", "B", "a", "b"], help="Optional player selector")
    parser.add_argument("--json", action="store_true", help="Emit structured JSON")
    parser.add_argument("--yaml", action="store_true", help="Emit structured YAML")
    args = parser.parse_args(argv)

    if args.json and args.yaml:
        return _emit_error("choose only one of --json or --yaml")

    fmt = default_output_format(args.json, args.yaml)

    # Mode 1: global player assignment shorthand, e.g. audio A=Spotify or audio B=52313
    if "=" in args.arg1 and args.arg2 is None and args.arg3 is None:
        left, right = args.arg1.split("=", 1)
        player = left.strip().upper()
        service_target = right.strip()
        if player not in {"A", "B"}:
            return _emit_error("audio player assignment must use A=<service> or B=<service>", fmt=fmt)
        if not service_target:
            return _emit_error("audio player assignment requires a service id or name", fmt=fmt)

        try:
            config = load_config()
            client = CrestronClient(config)
            state = load_state()
            if not has_cached_inventory(state):
                state = _refresh_inventory(client, state)

            services = _collect_audio_services(state)
            selected: Dict[str, Any] | None = None
            if service_target.isdigit():
                wanted = int(service_target)
                for service in services:
                    if int(service.get("service_id") or -1) == wanted:
                        selected = service
                        break
            else:
                wanted_name = service_target.strip().lower()
                # Prefer exact name match first, then fall back to partial contains match.
                for service in services:
                    if str(service.get("service_name") or "").strip().lower() == wanted_name:
                        selected = service
                        break
                if selected is None:
                    for service in services:
                        service_name = str(service.get("service_name") or "").strip().lower()
                        if wanted_name in service_name:
                            selected = service
                            break

            if selected is None:
                return _emit_error("audio default update failed", fmt=fmt, details=f"unknown service '{service_target}'")

            state = _set_audio_default(state, player, int(selected.get("service_id")), str(selected.get("service_name")))
            save_state(state)

            emit_payload(
                {
                    "success": True,
                    "message": f"Player {player} service set to {selected.get('service_name')}",
                    "data": {
                        "player": player,
                        "service_id": selected.get("service_id"),
                        "service_name": selected.get("service_name"),
                    },
                },
                fmt,
            )
            return 0
        except ConfigError as exc:
            return _emit_error(str(exc), fmt=fmt)
        except (CrestronApiError, StateError, RuntimeError) as exc:
            return _emit_error("audio default update failed", fmt=fmt, details=str(exc))

    # Mode 1b: legacy global assignment form, e.g. audio A "Spotify"
    if args.arg1.lower() in {"a", "b"} and args.arg2 is not None and args.arg3 is None and args.arg2.lower() not in {"on", "off", "set", "mute", "unmute", "toggle", "service"}:
        forwarded = [f"{args.arg1.upper()}={args.arg2}"]
        if args.json:
            forwarded.append("--json")
        if args.yaml:
            forwarded.append("--yaml")
        return _audio_command(forwarded)

    # Mode 2: room-targeted audio actions (formerly speaker actions)
    target = args.arg1
    action = (args.arg2 or "").lower()
    value = args.arg3
    if action.startswith("player=") and value is None:
        value = action.split("=", 1)[1].strip()
        action = "player"

    if action not in {"on", "off", "set", "mute", "unmute", "toggle", "service"}:
        if action != "player":
            return _emit_error(
                "audio action is required",
                fmt=fmt,
                details="use 'crestron-cli audio <target> on|off|set|mute|unmute|toggle|service|player' or 'crestron-cli audio A=<service>'",
            )

    player = args.player.upper() if args.player else None

    if action == "player":
        if value is None:
            return _emit_error("player action requires A or B", fmt=fmt)
        requested_player = value.strip().upper()
        if requested_player not in {"A", "B"}:
            return _emit_error("player action requires A or B", fmt=fmt)
        player = requested_player

    if action not in {"on", "off", "set", "mute", "unmute", "toggle", "service", "player"}:
        return _emit_error(
            "audio action is required",
            fmt=fmt,
            details="use 'crestron-cli audio <target> on|off|set|mute|unmute|toggle|service|player' or 'crestron-cli audio A=<service>'",
        )

    if action == "set":
        if value is None:
            return _emit_error("set requires level argument (0-100)", fmt=fmt)
        try:
            level_percent = int(value)
        except Exception:
            return _emit_error("level must be an integer between 0 and 100", fmt=fmt)
        if level_percent < 0 or level_percent > 100:
            return _emit_error("level must be between 0 and 100", fmt=fmt)
    else:
        level_percent = None

    if action == "service" and value is None:
        return _emit_error("service action requires a service id or name", fmt=fmt)

    if action not in {"set", "service", "player"} and value is not None:
        return _emit_error("unexpected value argument", fmt=fmt)

    try:
        config = load_config()
        client = CrestronClient(config)
        state = load_state()

        if not has_cached_inventory(state):
            state = _refresh_inventory(client, state)

        target_token = _normalize_speaker_target_token(target)
        speaker_id, speaker = resolve_speaker_target(state, target_token)
        room_id = speaker.get("room_id")
        current_power_state = str(speaker.get("current_power_state") or "").lower()

        selected_source_id: int | None = None
        selected_source_name: str | None = None
        effective_player: str | None = player
        expected_power_on = False

        if action == "on":
            expected_power_on = True
            if effective_player is None:
                effective_player = "A"
            preferred_source_id: int | None = None
            defaults = _get_audio_defaults(state)
            default_entry = defaults.get(effective_player) or {}
            try:
                preferred_source_id = int(default_entry.get("service_id")) if default_entry.get("service_id") is not None else None
            except Exception:
                preferred_source_id = None

            selected_source_id, selected_source_name = resolve_speaker_source_target(
                speaker,
                None,
                player=effective_player,
                preferred_source_id=preferred_source_id,
            )
            try:
                client.select_speaker_source(speaker_id, selected_source_id)
                state = update_speaker_state(state, speaker_id, source_id=selected_source_id)
            except CrestronApiError as exc:
                if exc.status_code != 409:
                    raise
            try:
                client.set_speaker_power(speaker_id, "on")
            except CrestronApiError as exc:
                if exc.status_code != 409:
                    raise
            state = update_speaker_state(state, speaker_id, power_state="on")
            action_desc = "turned on"
        elif action == "off":
            client.set_speaker_power(speaker_id, "off")
            state = update_speaker_state(state, speaker_id, power_state="off")
            action_desc = "turned off"
        elif action == "set":
            assert level_percent is not None
            client.set_speaker_volume(speaker_id, level_percent)
            state = update_speaker_state(state, speaker_id, volume_percent=level_percent)
            action_desc = f"set to {level_percent}%"
        elif action == "mute":
            client.mute_speaker(speaker_id)
            state = update_speaker_state(state, speaker_id, mute_state="muted")
            action_desc = "muted"
        elif action == "unmute":
            client.unmute_speaker(speaker_id)
            state = update_speaker_state(state, speaker_id, mute_state="unmuted")
            action_desc = "unmuted"
        elif action == "toggle":
            if current_power_state == "on":
                client.set_speaker_power(speaker_id, "off")
                state = update_speaker_state(state, speaker_id, power_state="off")
                action_desc = "turned off"
            else:
                expected_power_on = True
                if effective_player is None:
                    effective_player = "A"
                preferred_source_id = None
                defaults = _get_audio_defaults(state)
                default_entry = defaults.get(effective_player) or {}
                try:
                    preferred_source_id = int(default_entry.get("service_id")) if default_entry.get("service_id") is not None else None
                except Exception:
                    preferred_source_id = None

                selected_source_id, selected_source_name = resolve_speaker_source_target(
                    speaker,
                    None,
                    player=effective_player,
                    preferred_source_id=preferred_source_id,
                )

                try:
                    client.select_speaker_source(speaker_id, selected_source_id)
                    state = update_speaker_state(state, speaker_id, source_id=selected_source_id)
                except CrestronApiError as exc:
                    if exc.status_code != 409:
                        raise
                try:
                    client.set_speaker_power(speaker_id, "on")
                except CrestronApiError as exc:
                    if exc.status_code != 409:
                        raise
                state = update_speaker_state(state, speaker_id, power_state="on")
                action_desc = "turned on"
        elif action == "player":
            assert player in {"A", "B"}
            preferred_source_id = None
            defaults = _get_audio_defaults(state)
            default_entry = defaults.get(player) or {}
            try:
                preferred_source_id = int(default_entry.get("service_id")) if default_entry.get("service_id") is not None else None
            except Exception:
                preferred_source_id = None

            selected_source_id, selected_source_name = resolve_speaker_source_target(
                speaker,
                None,
                player=player,
                preferred_source_id=preferred_source_id,
            )
            client.select_speaker_source(speaker_id, selected_source_id)
            state = update_speaker_state(state, speaker_id, source_id=selected_source_id)
            action_desc = f"player set to {player}"
        else:
            assert action == "service"
            selected_source_id, selected_source_name = resolve_speaker_source_target(
                speaker,
                value,
                player=player,
            )
            client.select_speaker_source(speaker_id, selected_source_id)
            state = update_speaker_state(state, speaker_id, source_id=selected_source_id)
            if player in {"A", "B"}:
                state = _set_audio_default(state, player, selected_source_id, selected_source_name)
            action_desc = f"service set to {selected_source_name or selected_source_id}"

        observed_from_refresh = True
        try:
            state = _refresh_inventory(client, state)
        except Exception:
            observed_from_refresh = False
            save_state(state)

        # Some controllers treat /volume/{value} as raw 0..65535. If percent write
        # did not stick, retry once using raw scaling and refresh again.
        if level_percent is not None and observed_from_refresh:
            current_after_write = ((state.get("speakers") or {}).get("by_id") or {}).get(str(speaker_id)) or {}
            observed_level = current_after_write.get("current_volume_percent")
            observed_level_percent: int | None
            try:
                observed_level_percent = int(round(float(observed_level))) if observed_level is not None else None
            except Exception:
                observed_level_percent = None

            power_after_write = str(current_after_write.get("current_power_state") or "").lower()
            if (
                observed_level_percent is not None
                and power_after_write == "on"
                and abs(observed_level_percent - level_percent) >= 5
            ):
                try:
                    client.set_speaker_volume_raw(speaker_id, percent_to_raw(level_percent))
                    state = _refresh_inventory(client, state)
                except Exception:
                    pass

        speaker_name = str(speaker.get("name") or f"Speaker {speaker_id}")
        current_speaker = ((state.get("speakers") or {}).get("by_id") or {}).get(str(speaker_id)) or {}
        current_source_id = current_speaker.get("current_source_id")
        current_mute_state = str(current_speaker.get("current_mute_state") or "").lower() or None
        current_power_state = str(current_speaker.get("current_power_state") or "").lower() or None
        current_source_name: str | None = None
        current_player: str | None = None
        for src in current_speaker.get("available_sources") or []:
            if not isinstance(src, dict):
                continue
            try:
                src_id = int(src.get("id")) if src.get("id") is not None else None
            except Exception:
                src_id = None
            if src_id is not None and current_source_id is not None and int(current_source_id) == src_id:
                current_source_name = str(src.get("source_name") or "")
                lowered = current_source_name.lower()
                if lowered.startswith("player a"):
                    current_player = "A"
                elif lowered.startswith("player b"):
                    current_player = "B"
                break

        payload_data: Dict[str, Any] = {
            "id": speaker_id,
            "name": speaker_name,
            "action": action,
            "room_id": room_id,
        }
        if level_percent is not None:
            payload_data["level_percent"] = level_percent
        if selected_source_id is not None:
            payload_data["service_id"] = selected_source_id
        if selected_source_name:
            payload_data["service_name"] = selected_source_name
        if effective_player is not None:
            payload_data["player"] = effective_player
        if current_power_state is not None:
            payload_data["current_power_state"] = current_power_state
            payload_data["current_state"] = current_power_state
        else:
            payload_data["current_state"] = "unknown"
        if current_mute_state is not None:
            payload_data["current_mute_state"] = current_mute_state
        if current_source_id is not None:
            payload_data["current_service_id"] = current_source_id
            payload_data["current_service_name"] = current_source_name or "unknown"
            payload_data["current_player"] = current_player or "unknown"
        payload_data["observed_from_refresh"] = observed_from_refresh

        if expected_power_on and current_power_state != "on":
            return _emit_error(
                "audio action failed",
                fmt=fmt,
                details="power on was not confirmed by observed state",
            )

        emit_payload(
            {
                "success": True,
                "message": f"{speaker_name} {action_desc}",
                "data": payload_data,
            },
            fmt,
        )
        return 0
    except ConfigError as exc:
        return _emit_error(str(exc), fmt=fmt)
    except (CrestronApiError, StateError, RuntimeError) as exc:
        return _emit_error("audio action failed", fmt=fmt, details=str(exc))


def _speaker_command(argv: List[str]) -> int:
    return _audio_command(argv)


def _extract_output_mode(tokens: List[str]) -> Tuple[List[str], str, str | None]:
    remaining: List[str] = []
    json_flag = False
    yaml_flag = False
    for token in tokens:
        lowered = token.lower()
        if lowered == "--json":
            json_flag = True
            continue
        if lowered == "--yaml":
            yaml_flag = True
            continue
        if token.startswith("--"):
            return [], "human", f"unknown option '{token}'"
        remaining.append(token)

    if json_flag and yaml_flag:
        return [], "human", "choose only one of --json or --yaml"
    return remaining, default_output_format(json_flag, yaml_flag), None


def _parse_key_value(tokens: List[str], key: str) -> Tuple[List[str], str | None, str | None]:
    key_lower = key.lower()
    out: List[str] = []
    value: str | None = None
    idx = 0
    while idx < len(tokens):
        token = tokens[idx]
        lowered = token.lower()
        if lowered.startswith(f"{key_lower}="):
            candidate = token.split("=", 1)[1].strip()
            if not candidate:
                return [], None, f"{key} requires a value"
            if value is not None and value.lower() != candidate.lower():
                return [], None, f"conflicting {key} values provided"
            value = candidate
        elif lowered == key_lower:
            if idx + 1 >= len(tokens):
                return [], None, f"{key} requires a value"
            candidate = tokens[idx + 1].strip()
            if not candidate:
                return [], None, f"{key} requires a value"
            if value is not None and value.lower() != candidate.lower():
                return [], None, f"conflicting {key} values provided"
            value = candidate
            idx += 1
        else:
            out.append(token)
        idx += 1
    return out, value, None


def _print_target_help(kind: str, target: str | None = None) -> None:
    if kind == "light":
        print(
            "\n".join(
                [
                    f"crestron-cli light={target or '<id|name>'}",
                    "",
                    "Usage:",
                    "  crestron-cli light=<id|name> on|off|toggle|level=<0..100> [--json|--yaml]",
                    "",
                    "Notes:",
                    "  - Action tokens are case-insensitive",
                    "  - level=0..100 is integer percent",
                    "  - If level is provided while off, load turns on at that level",
                ]
            )
        )
        return

    if kind == "audio-target":
        print(
            "\n".join(
                [
                    f"crestron-cli audio={target or '<id|name>'}",
                    "",
                    "Usage:",
                    "  crestron-cli audio=<id|name> [on|off|toggle] [level=<0..100>] [mute|unmute] [player=<A|B>] [--json|--yaml]",
                    "",
                    "Notes:",
                    "  - Action tokens are case-insensitive",
                    "  - Conflicting tokens (on+off, mute+unmute, etc.) raise an error",
                    "  - If level is provided while off, route turns on at that level",
                    "  - If powering on and player is omitted, defaults to player A",
                ]
            )
        )
        return

    if kind == "audio-global":
        print(
            "\n".join(
                [
                    "crestron-cli audio",
                    "",
                    "Player service assignment:",
                    "  crestron-cli audio A=<service-id|service-name|partial-name> [--json|--yaml]",
                    "  crestron-cli audio B=<service-id|service-name|partial-name> [--json|--yaml]",
                    "",
                    "Room routing/control:",
                    "  crestron-cli audio=<id|name> [on|off|toggle] [level=<0..100>] [mute|unmute] [player=<A|B>] [--json|--yaml]",
                    "",
                    "Discovery:",
                    "  crestron-cli query audio service",
                    "  crestron-cli query audio player",
                    "",
                    "Notes:",
                    "  - Name matching is case-insensitive",
                    "  - Partial matching is supported",
                    "  - Matches are player-scoped and use the same dataset as 'query audio service'",
                    "  - Ambiguous service matches return an error; use service-id for deterministic control",
                ]
            )
        )
        return

    if kind == "scene":
        print(
            "\n".join(
                [
                    f"crestron-cli scene={target or '<id|name>'}",
                    "",
                    "Usage:",
                    "  crestron-cli scene=<id|name> on|activate [--type <lighting|media>] [--room-id <id>] [--json|--yaml]",
                    "",
                    "Notes:",
                    "  - Action tokens are case-insensitive",
                    "  - on and activate are equivalent",
                ]
            )
        )


def _handle_light_target(target: str, argv: List[str]) -> int:
    if any(flag in argv for flag in ("-h", "--help")):
        _print_target_help("light", target)
        return 0

    tokens, fmt, parse_error = _extract_output_mode(argv)
    if parse_error:
        return _emit_error(parse_error)

    tokens, level_text, kv_error = _parse_key_value(tokens, "level")
    if kv_error:
        return _emit_error(kv_error, fmt=fmt)

    action_tokens = [token.lower().strip() for token in tokens if token.strip()]
    on_flag = "on" in action_tokens
    off_flag = "off" in action_tokens
    toggle_flag = "toggle" in action_tokens

    if on_flag and off_flag:
        return _emit_error("conflicting actions: on and off", fmt=fmt)
    if toggle_flag and (on_flag or off_flag):
        return _emit_error("conflicting actions: toggle cannot be combined with on/off", fmt=fmt)

    level_percent: int | None = None
    if level_text is not None:
        try:
            level_percent = int(level_text)
        except Exception:
            return _emit_error("level must be an integer between 0 and 100", fmt=fmt)
        if level_percent < 0 or level_percent > 100:
            return _emit_error("level must be between 0 and 100", fmt=fmt)

    if not any([on_flag, off_flag, toggle_flag, level_percent is not None]):
        return _emit_error("light action is required", fmt=fmt, details="use on|off|toggle and/or level=<0..100>")

    try:
        config = load_config()
        client = CrestronClient(config)
        state = load_state()
        if not has_cached_inventory(state):
            state = _refresh_inventory(client, state)

        target_token = _normalize_target_token(target)
        light_id, light = resolve_light_target(state, target_token)
        current_level = int(light.get("current_level") or 0)

        if toggle_flag:
            target_raw = 0 if current_level > 0 else 65535
        elif off_flag:
            target_raw = 0
        elif level_percent is not None:
            target_raw = percent_to_raw(level_percent)
        else:
            target_raw = 65535

        client.set_light_state(light_id=light_id, level_raw=target_raw)

        observed_from_refresh = True
        try:
            state = _refresh_inventory(client, state)
        except Exception:
            observed_from_refresh = False
            state = update_light_level(state, light_id=light_id, level_raw=target_raw)
            save_state(state)

        current_light = ((state.get("lights") or {}).get("by_id") or {}).get(str(light_id)) or {}
        observed_level = current_light.get("percent")
        try:
            observed_level_percent = int(round(float(observed_level))) if observed_level is not None else None
        except Exception:
            observed_level_percent = None

        current_state = "unknown"
        try:
            current_state = "on" if int(current_light.get("current_level") or 0) > 0 else "off"
        except Exception:
            pass

        emit_payload(
            {
                "success": True,
                "message": f"{light.get('name') or f'Light {light_id}'} updated",
                "data": {
                    "object": "light",
                    "id": light_id,
                    "name": light.get("name") or f"Light {light_id}",
                    "current_state": current_state,
                    "level_percent": observed_level_percent,
                    "observed_from_refresh": observed_from_refresh,
                },
            },
            fmt,
        )
        return 0
    except ConfigError as exc:
        return _emit_error(str(exc), fmt=fmt)
    except (CrestronApiError, StateError, RuntimeError) as exc:
        return _emit_error("light action failed", fmt=fmt, details=str(exc))


def _handle_audio_global_assignment(argv: List[str]) -> int:
    if any(flag in argv for flag in ("-h", "--help")):
        _print_target_help("audio-global")
        return 0

    tokens, fmt, parse_error = _extract_output_mode(argv)
    if parse_error:
        return _emit_error(parse_error)
    if len(tokens) != 1 or "=" not in tokens[0]:
        return _emit_error("audio player assignment must use A=<service> or B=<service>", fmt=fmt)

    left, right = tokens[0].split("=", 1)
    player = left.strip().upper()
    service_target = right.strip()
    if player not in {"A", "B"}:
        return _emit_error("audio player assignment must use A=<service> or B=<service>", fmt=fmt)
    if not service_target:
        return _emit_error("audio player assignment requires a service id or name", fmt=fmt)

    try:
        config = load_config()
        client = CrestronClient(config)
        state = load_state()
        if not has_cached_inventory(state):
            state = _refresh_inventory(client, state)

        # Resolve from the same service dataset exposed by `query audio service`.
        all_services = _collect_audio_services(state)
        services = [
            service
            for service in all_services
            if str(service.get("player") or "").strip().lower() == f"player {player}".lower()
        ]
        selected: Dict[str, Any] | None = None
        if service_target.isdigit():
            wanted = int(service_target)
            for service in services:
                if int(service.get("service_id") or -1) == wanted:
                    selected = service
                    break
        else:
            wanted_name = service_target.strip().lower()
            exact_matches = [
                service
                for service in services
                if str(service.get("service_name") or "").strip().lower() == wanted_name
            ]
            if len(exact_matches) == 1:
                selected = exact_matches[0]
            elif len(exact_matches) > 1:
                ids = ", ".join(str(match.get("service_id")) for match in exact_matches)
                return _emit_error(
                    "audio player assignment failed",
                    fmt=fmt,
                    details=f"ambiguous service name '{service_target}' for Player {player}; use service id ({ids})",
                )

            if selected is None:
                partial_matches = [
                    service
                    for service in services
                    if wanted_name in str(service.get("service_name") or "").strip().lower()
                ]
                if len(partial_matches) == 1:
                    selected = partial_matches[0]
                elif len(partial_matches) > 1:
                    ids = ", ".join(str(match.get("service_id")) for match in partial_matches)
                    return _emit_error(
                        "audio player assignment failed",
                        fmt=fmt,
                        details=f"ambiguous service match '{service_target}' for Player {player}; use service id ({ids})",
                    )

        if selected is None:
            return _emit_error("audio player assignment failed", fmt=fmt, details=f"unknown service '{service_target}'")

        state = _set_audio_default(state, player, int(selected.get("service_id")), str(selected.get("service_name")))
        save_state(state)

        emit_payload(
            {
                "success": True,
                "message": f"Player {player} service set",
                "data": {
                    "object": "audio-player",
                    "player": player,
                    "service_id": selected.get("service_id"),
                    "service_name": selected.get("service_name"),
                },
            },
            fmt,
        )
        return 0
    except ConfigError as exc:
        return _emit_error(str(exc), fmt=fmt)
    except (CrestronApiError, StateError, RuntimeError) as exc:
        return _emit_error("audio player assignment failed", fmt=fmt, details=str(exc))


def _handle_audio_target(target: str, argv: List[str]) -> int:
    if any(flag in argv for flag in ("-h", "--help")):
        _print_target_help("audio-target", target)
        return 0

    tokens, fmt, parse_error = _extract_output_mode(argv)
    if parse_error:
        return _emit_error(parse_error)

    tokens, level_text, kv_error = _parse_key_value(tokens, "level")
    if kv_error:
        return _emit_error(kv_error, fmt=fmt)
    tokens, player_text, kv_error = _parse_key_value(tokens, "player")
    if kv_error:
        return _emit_error(kv_error, fmt=fmt)

    action_tokens = [token.lower().strip() for token in tokens if token.strip()]
    unsupported = [token for token in action_tokens if token not in {"on", "off", "toggle", "mute", "unmute"}]
    if unsupported:
        return _emit_error("unsupported audio action token", fmt=fmt, details=", ".join(sorted(set(unsupported))))

    on_flag = "on" in action_tokens
    off_flag = "off" in action_tokens
    toggle_flag = "toggle" in action_tokens
    mute_flag = "mute" in action_tokens
    unmute_flag = "unmute" in action_tokens

    if on_flag and off_flag:
        return _emit_error("conflicting actions: on and off", fmt=fmt)
    if toggle_flag and (on_flag or off_flag):
        return _emit_error("conflicting actions: toggle cannot be combined with on/off", fmt=fmt)
    if mute_flag and unmute_flag:
        return _emit_error("conflicting actions: mute and unmute", fmt=fmt)

    level_percent: int | None = None
    if level_text is not None:
        try:
            level_percent = int(level_text)
        except Exception:
            return _emit_error("level must be an integer between 0 and 100", fmt=fmt)
        if level_percent < 0 or level_percent > 100:
            return _emit_error("level must be between 0 and 100", fmt=fmt)

    requested_player: str | None = None
    if player_text is not None:
        requested_player = player_text.strip().upper()
        if requested_player not in {"A", "B"}:
            return _emit_error("player must be A or B", fmt=fmt)

    if not any([on_flag, off_flag, toggle_flag, mute_flag, unmute_flag, level_percent is not None, requested_player is not None]):
        return _emit_error("audio action is required", fmt=fmt, details="use on|off|toggle|mute|unmute and/or level=<0..100> player=<A|B>")

    if off_flag and level_percent is not None:
        return _emit_error("conflicting actions: off cannot be combined with level", fmt=fmt)

    try:
        config = load_config()
        client = CrestronClient(config)
        state = load_state()
        if not has_cached_inventory(state):
            state = _refresh_inventory(client, state)

        speaker_id, speaker = resolve_speaker_target(state, _normalize_speaker_target_token(target))
        current_power_state = str(speaker.get("current_power_state") or "").lower()

        if toggle_flag:
            if current_power_state == "on":
                off_flag = True
            else:
                on_flag = True

        if level_percent is not None and current_power_state != "on" and not off_flag:
            on_flag = True

        effective_player: str | None = requested_player
        if on_flag and effective_player is None:
            effective_player = "A"

        selected_source_id: int | None = None
        selected_source_name: str | None = None
        if effective_player in {"A", "B"}:
            preferred_source_id: int | None = None
            room_preferred_source_id: int | None = None
            room_id = speaker.get("room_id")
            try:
                room_preferred_source_id = get_speaker_player_default(state, int(room_id), effective_player) if room_id is not None else None
            except Exception:
                room_preferred_source_id = None
            defaults = _get_audio_defaults(state)
            default_entry = defaults.get(effective_player) or {}
            try:
                preferred_source_id = int(default_entry.get("service_id")) if default_entry.get("service_id") is not None else None
            except Exception:
                preferred_source_id = None

            # Explicit player requests should follow the current global player
            # mapping; room presets are only used for implicit routing.
            effective_preferred_source_id = (
                preferred_source_id
                if requested_player is not None
                else (room_preferred_source_id if room_preferred_source_id is not None else preferred_source_id)
            )

            selected_source_id, selected_source_name = resolve_speaker_source_target(
                speaker,
                None,
                player=effective_player,
                preferred_source_id=effective_preferred_source_id,
            )

        if on_flag:
            try:
                client.set_speaker_power(speaker_id, "on")
            except CrestronApiError as exc:
                if exc.status_code != 409:
                    raise

        if selected_source_id is not None and (effective_player is not None):
            try:
                client.select_speaker_source(speaker_id, selected_source_id)
            except CrestronApiError as exc:
                if exc.status_code != 409:
                    raise

        requested_volume_raw: int | None = None
        if level_percent is not None:
            requested_volume_raw = percent_to_raw(level_percent)
            client.set_speaker_volume_raw(speaker_id, requested_volume_raw)

        if mute_flag:
            client.mute_speaker(speaker_id)
        if unmute_flag:
            client.unmute_speaker(speaker_id)

        if off_flag:
            client.set_speaker_power(speaker_id, "off")

        observed_from_refresh = True
        try:
            state = _refresh_inventory(client, state)
        except Exception:
            observed_from_refresh = False
            save_state(state)

        current = ((state.get("speakers") or {}).get("by_id") or {}).get(str(speaker_id)) or {}
        observed_level_percent: int | None = None
        try:
            observed_level_percent = (
                int(round(float(current.get("current_volume_percent"))))
                if current.get("current_volume_percent") is not None
                else None
            )
        except Exception:
            observed_level_percent = None

        # Some controllers interpret /volume/{value} as percent; others as raw.
        # We write raw first, then retry percent once if readback still mismatches.
        if (
            level_percent is not None
            and observed_from_refresh
            and str(current.get("current_power_state") or "").lower() == "on"
            and observed_level_percent is not None
            and requested_volume_raw is not None
            and observed_level_percent < level_percent
            and requested_volume_raw < 65535
        ):
            try:
                client.set_speaker_volume_raw(speaker_id, requested_volume_raw + 1)
                state = _refresh_inventory(client, state)
                current = ((state.get("speakers") or {}).get("by_id") or {}).get(str(speaker_id)) or {}
                try:
                    observed_level_percent = (
                        int(round(float(current.get("current_volume_percent"))))
                        if current.get("current_volume_percent") is not None
                        else None
                    )
                except Exception:
                    observed_level_percent = None
            except Exception:
                pass

        if (
            level_percent is not None
            and observed_from_refresh
            and str(current.get("current_power_state") or "").lower() == "on"
            and (observed_level_percent is None or abs(observed_level_percent - level_percent) >= 5)
        ):
            try:
                client.set_speaker_volume(speaker_id, level_percent)
                state = _refresh_inventory(client, state)
                current = ((state.get("speakers") or {}).get("by_id") or {}).get(str(speaker_id)) or {}
                try:
                    observed_level_percent = (
                        int(round(float(current.get("current_volume_percent"))))
                        if current.get("current_volume_percent") is not None
                        else None
                    )
                except Exception:
                    observed_level_percent = None
            except Exception:
                pass

        reported_level_percent = observed_level_percent if observed_level_percent is not None else level_percent

        current_source_id = current.get("current_source_id")
        current_source_name: str | None = None
        current_player = "unknown"
        for src in current.get("available_sources") or []:
            if not isinstance(src, dict):
                continue
            try:
                sid = int(src.get("id")) if src.get("id") is not None else None
            except Exception:
                sid = None
            if sid is not None and current_source_id is not None and int(current_source_id) == sid:
                current_source_name = str(src.get("source_name") or "")
                current_player = _infer_player_from_source_name(current_source_name)
                break

        # Learn room-specific service IDs per player so subsequent routes reuse
        # the channel that actually works for this room/amp path.
        room_id_for_preset = current.get("room_id") or speaker.get("room_id")
        if current_source_id is not None and current_player in {"A", "B"} and room_id_for_preset is not None:
            try:
                state = set_speaker_player_default(state, int(room_id_for_preset), current_player, int(current_source_id))
                save_state(state)
            except Exception:
                pass

        emit_payload(
            {
                "success": True,
                "message": f"{speaker.get('name') or f'Audio {speaker_id}'} updated",
                "data": {
                    "object": "audio",
                    "id": speaker_id,
                    "name": speaker.get("name") or f"Audio {speaker_id}",
                    "current_state": str(current.get("current_power_state") or "unknown"),
                    "level_percent": reported_level_percent,
                    "mute": str(current.get("current_mute_state") or "unknown"),
                    "player": current_player,
                    "service_id": current_source_id,
                    "service_name": _strip_player_prefix(current_source_name) if current_source_name else None,
                    "observed_from_refresh": observed_from_refresh,
                },
            },
            fmt,
        )
        return 0
    except ConfigError as exc:
        return _emit_error(str(exc), fmt=fmt)
    except (CrestronApiError, StateError, RuntimeError) as exc:
        return _emit_error("audio action failed", fmt=fmt, details=str(exc))


def _handle_scene_target(target: str, argv: List[str]) -> int:
    if any(flag in argv for flag in ("-h", "--help")):
        _print_target_help("scene", target)
        return 0

    json_flag = False
    yaml_flag = False
    tokens: List[str] = []
    for token in argv:
        lowered = token.lower()
        if lowered == "--json":
            json_flag = True
            continue
        if lowered == "--yaml":
            yaml_flag = True
            continue
        tokens.append(token)

    if json_flag and yaml_flag:
        return _emit_error("choose only one of --json or --yaml")
    fmt = default_output_format(json_flag, yaml_flag)

    scene_type: str | None = None
    room_id: int | None = None
    actions: List[str] = []

    idx = 0
    while idx < len(tokens):
        token = tokens[idx]
        lowered = token.lower()
        if lowered == "--type":
            if idx + 1 >= len(tokens):
                return _emit_error("--type requires lighting or media", fmt=fmt)
            scene_type = tokens[idx + 1].strip().lower()
            idx += 2
            continue
        if lowered.startswith("--type="):
            scene_type = token.split("=", 1)[1].strip().lower()
            idx += 1
            continue
        if lowered == "--room-id":
            if idx + 1 >= len(tokens):
                return _emit_error("--room-id requires an integer value", fmt=fmt)
            try:
                room_id = int(tokens[idx + 1].strip())
            except Exception:
                return _emit_error("--room-id requires an integer value", fmt=fmt)
            idx += 2
            continue
        if lowered.startswith("--room-id="):
            try:
                room_id = int(token.split("=", 1)[1].strip())
            except Exception:
                return _emit_error("--room-id requires an integer value", fmt=fmt)
            idx += 1
            continue
        if token.startswith("--"):
            return _emit_error(f"unknown option '{token}'", fmt=fmt)
        actions.append(lowered)
        idx += 1

    on_flag = "on" in actions or "activate" in actions
    if not on_flag:
        return _emit_error("scene action is required", fmt=fmt, details="use on or activate")

    unsupported = [token for token in actions if token not in {"on", "activate"}]
    if unsupported:
        return _emit_error("unsupported scene action token", fmt=fmt, details=", ".join(sorted(set(unsupported))))

    if scene_type is not None and scene_type not in {"lighting", "media"}:
        return _emit_error("--type must be lighting or media", fmt=fmt)

    try:
        config = load_config()
        client = CrestronClient(config)
        state = load_state()
        if not has_cached_inventory(state):
            state = _refresh_inventory(client, state)

        scene_id, scene = resolve_scene_target(state, _normalize_scene_target_token(target), scene_type=scene_type, room_id=room_id)
        client.recall_scene(scene_id=scene_id)

        emit_payload(
            {
                "success": True,
                "message": f"Scene {scene.get('name') or scene_id} activated",
                "data": {
                    "object": "scene",
                    "id": scene_id,
                    "name": scene.get("name") or f"Scene {scene_id}",
                    "current_state": "activated",
                    "scene_type": scene.get("scene_type"),
                },
            },
            fmt,
        )
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
            "  crestron-cli query [lights|scenes|audio] [room=<id|name>] [player|service] [--refresh] [--raw|--json|--yaml]",
            "  crestron-cli query room=<id|name> [lights|scenes|audio] [player|service] [--refresh] [--raw|--json|--yaml]",
            "  crestron-cli query rooms [--refresh] [--raw|--json|--yaml]",
            "  crestron-cli query audio [room=<id|name>|player|service] [--refresh] [--raw|--json|--yaml]",
            "  crestron-cli light=<id|name> on|off|toggle|level=<0..100> [--json|--yaml]",
            "  crestron-cli audio=<id|name> [on|off|toggle] [level=<0..100>] [mute|unmute] [player=<A|B>] [--json|--yaml]",
            "  crestron-cli scene=<id|name> on|activate [--type <lighting|media>] [--room-id <id>] [--json|--yaml]",
            "  crestron-cli audio A=<service-id|service-name|partial-name> [--json|--yaml]",
            "  crestron-cli audio B=<service-id|service-name|partial-name> [--json|--yaml]",
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
    command_lower = command.lower()

    if "=" in command:
        key, value = command.split("=", 1)
        key_lower = key.strip().lower()
        target_value = value.strip()
        if not target_value:
            return _emit_error(f"{key_lower} target cannot be empty")
        if key_lower == "light":
            return _handle_light_target(target_value, argv[1:])
        if key_lower == "audio":
            return _handle_audio_target(target_value, argv[1:])
        if key_lower == "scene":
            return _handle_scene_target(target_value, argv[1:])

    if command_lower == "initialize":
        return _initialize_command(argv[1:])
    if command_lower == "query":
        return _query_command(argv[1:])
    if command_lower == "audio":
        return _handle_audio_global_assignment(argv[1:])

    return _emit_error(f"unknown command '{command}'", details="run 'crestron-cli --help' for usage")


def cli() -> None:
    raise SystemExit(main())


if __name__ == "__main__":
    raise SystemExit(main())
