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
    list_speakers,
    load_state,
    resolve_light_target,
    resolve_room_target,
    resolve_scene_target,
    resolve_speaker_source_target,
    resolve_speaker_target,
    save_state,
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
            if existing.get("source_id") is None:
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
                return "", None, None, "multiple audio selectors provided; use only one of player or source"
            audio_view = "player"
            continue

        if normalized in {"source", "sources"}:
            if audio_view is not None:
                return "", None, None, "multiple audio selectors provided; use only one of player or source"
            audio_view = "source"
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
        return "", None, None, "audio player/source selectors are only valid with query audio"

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
        elif audio_view == "source":
            view_hint = "\nSelected view: source"
        text = "\n".join(
            [
                "crestron-cli query audio",
                "",
                "Usage:",
                "  crestron-cli query audio [room=<id|name>] [--refresh] [--raw|--json|--yaml]",
                "  crestron-cli query audio player [--refresh] [--raw|--json|--yaml]",
                "  crestron-cli query audio source [room=<id|name>] [--refresh] [--raw|--json|--yaml]",
                "  crestron-cli query room=<id|name> audio [player|source] [--refresh] [--raw|--json|--yaml]",
                "",
                "Views:",
                "  audio         Room audio status (name, power, mute, volume %, player)",
                "  audio player  Global Player A/B source mapping",
                "  audio source  Available source names and source IDs",
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


def _set_audio_default(state: Dict[str, Any], player: str, source_id: int, source_name: str) -> Dict[str, Any]:
    defaults = _get_audio_defaults(state)
    defaults[player] = {
        "source_id": int(source_id),
        "source_name": str(source_name),
    }
    return state


def _collect_audio_sources(state: Dict[str, Any], room_id: int | None = None) -> List[Dict[str, Any]]:
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
            source_name = str(source.get("source_name") or "")
            by_id[source_id] = {
                "source_id": source_id,
                "source_name": _strip_player_prefix(source_name) or source_name,
            }
    items = list(by_id.values())
    items.sort(key=lambda row: (str(row.get("source_name") or "").lower(), int(row.get("source_id") or 0)))
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

        items.append(
            {
                "name": speaker.get("name"),
                "id": speaker.get("id"),
                "current_power_state": speaker.get("current_power_state") or "unknown",
                "current_mute_state": speaker.get("current_mute_state") or "unknown",
                "current_volume_percent": volume_percent,
                "player": _infer_player_from_source_name(current_source_name),
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
        source_id = entry.get("source_id")
        source_name = entry.get("source_name")
        items.append(
            {
                "player": f"Player {player}",
                "source": _strip_player_prefix(str(source_name) if source_name else None) or "unset",
                "source_id": source_id,
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
        if items and "player" in items[0] and "source" in items[0]:
            headers = ["Player", "Source"]
            rows = [[row.get("player"), row.get("source")] for row in items]
            print(f"Audio players ({len(items)})\n{render_table(headers, rows)}")
            return

        if items and "source_id" in items[0] and "source_name" in items[0] and "player" not in items[0]:
            headers = ["Sources", "Source ID"]
            rows = [[row.get("source_name"), row.get("source_id")] for row in items]
            print(f"Audio sources ({len(items)})\n{render_table(headers, rows)}")
            return

        headers = ["Name", "Speaker ID", "Power", "Mute", "Volume %", "Player"]
        rows = [
            [
                row.get("name"),
                row.get("id"),
                row.get("current_power_state"),
                row.get("current_mute_state"),
                row.get("current_volume_percent"),
                row.get("player"),
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
        if items and "player" in items[0] and "source" in items[0]:
            headers = ["Player", "Source"]
            rows = [[row.get("player"), row.get("source")] for row in items]
            print(render_csv(headers, rows))
            return

        if items and "source_id" in items[0] and "source_name" in items[0] and "player" not in items[0]:
            headers = ["Sources", "Source ID"]
            rows = [[row.get("source_name"), row.get("source_id")] for row in items]
            print(render_csv(headers, rows))
            return

        headers = ["Name", "Speaker ID", "Power", "Mute", "Volume %", "Player"]
        rows = [
            [
                row.get("name"),
                row.get("id"),
                row.get("current_power_state"),
                row.get("current_mute_state"),
                row.get("current_volume_percent"),
                row.get("player"),
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
        if items and "player" in items[0] and "source" in items[0]:
            preferred = ["player", "source", "source_id"]
        elif items and "source_id" in items[0] and "source_name" in items[0] and "player" not in items[0]:
            preferred = ["source_name", "source_id"]
        else:
            preferred = [
                "name",
                "id",
                "current_power_state",
                "current_mute_state",
                "current_volume_percent",
                "player",
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
    parser.add_argument("token3", nargs="?", help="Optional audio selector: player|source")
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
        if args.refresh or not has_cached_inventory(state):
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
            elif audio_view == "source":
                items = _collect_audio_sources(state, room_id=room_id)
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
        usage="crestron-cli audio <target> {on|off|set|mute|unmute|toggle|source|player} [value] [--player <A|B>] [--json|--yaml]\n       crestron-cli audio <A|B>=<source-id|source-name>",
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
        source_target = right.strip()
        if player not in {"A", "B"}:
            return _emit_error("audio player assignment must use A=<source> or B=<source>", fmt=fmt)
        if not source_target:
            return _emit_error("audio player assignment requires a source id or name", fmt=fmt)

        try:
            config = load_config()
            client = CrestronClient(config)
            state = load_state()
            if not has_cached_inventory(state):
                state = _refresh_inventory(client, state)

            sources = _collect_audio_sources(state)
            selected: Dict[str, Any] | None = None
            if source_target.isdigit():
                wanted = int(source_target)
                for source in sources:
                    if int(source.get("source_id") or -1) == wanted:
                        selected = source
                        break
            else:
                wanted_name = source_target.strip().lower()
                for source in sources:
                    if str(source.get("source_name") or "").strip().lower() == wanted_name:
                        selected = source
                        break

            if selected is None:
                return _emit_error("audio default update failed", fmt=fmt, details=f"unknown source '{source_target}'")

            state = _set_audio_default(state, player, int(selected.get("source_id")), str(selected.get("source_name")))
            save_state(state)

            emit_payload(
                {
                    "success": True,
                    "message": f"Player {player} source set to {selected.get('source_name')}",
                    "data": {
                        "player": player,
                        "source_id": selected.get("source_id"),
                        "source_name": selected.get("source_name"),
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
    if args.arg1.lower() in {"a", "b"} and args.arg2 is not None and args.arg3 is None and args.arg2.lower() not in {"on", "off", "set", "mute", "unmute", "toggle", "source"}:
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

    if action not in {"on", "off", "set", "mute", "unmute", "toggle", "source"}:
        if action != "player":
            return _emit_error(
                "audio action is required",
                fmt=fmt,
                details="use 'crestron-cli audio <target> on|off|set|mute|unmute|toggle|source|player' or 'crestron-cli audio A=<source>'",
            )

    player = args.player.upper() if args.player else None

    if action == "player":
        if value is None:
            return _emit_error("player action requires A or B", fmt=fmt)
        requested_player = value.strip().upper()
        if requested_player not in {"A", "B"}:
            return _emit_error("player action requires A or B", fmt=fmt)
        player = requested_player

    if action not in {"on", "off", "set", "mute", "unmute", "toggle", "source", "player"}:
        return _emit_error(
            "audio action is required",
            fmt=fmt,
            details="use 'crestron-cli audio <target> on|off|set|mute|unmute|toggle|source|player' or 'crestron-cli audio A=<source>'",
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

    if action == "source" and value is None:
        return _emit_error("source action requires a source id or name", fmt=fmt)

    if action not in {"set", "source", "player"} and value is not None:
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
                preferred_source_id = int(default_entry.get("source_id")) if default_entry.get("source_id") is not None else None
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
                    preferred_source_id = int(default_entry.get("source_id")) if default_entry.get("source_id") is not None else None
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
                preferred_source_id = int(default_entry.get("source_id")) if default_entry.get("source_id") is not None else None
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
            assert action == "source"
            selected_source_id, selected_source_name = resolve_speaker_source_target(
                speaker,
                value,
                player=player,
            )
            client.select_speaker_source(speaker_id, selected_source_id)
            state = update_speaker_state(state, speaker_id, source_id=selected_source_id)
            if player in {"A", "B"}:
                state = _set_audio_default(state, player, selected_source_id, selected_source_name)
            action_desc = f"source set to {selected_source_name or selected_source_id}"

        observed_from_refresh = True
        try:
            state = _refresh_inventory(client, state)
        except Exception:
            observed_from_refresh = False
            save_state(state)

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
            payload_data["source_id"] = selected_source_id
        if selected_source_name:
            payload_data["source_name"] = selected_source_name
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
            payload_data["current_source_id"] = current_source_id
            payload_data["current_source_name"] = current_source_name or "unknown"
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


def _print_root_help() -> None:
    text = "\n".join(
        [
            "crestron-cli",
            "",
            "Usage:",
            "  crestron-cli initialize [--force] [--verbose] [--json|--yaml]",
            "  crestron-cli query [lights|scenes|audio] [room=<id|name>] [player|source] [--refresh] [--raw|--json|--yaml]",
            "  crestron-cli query room=<id|name> [lights|scenes|audio] [player|source] [--refresh] [--raw|--json|--yaml]",
            "  crestron-cli query rooms [--refresh] [--raw|--json|--yaml]",
            "  crestron-cli query audio [room=<id|name>|player|source] [--refresh] [--raw|--json|--yaml]",
            "  crestron-cli scene <target> {on|activate} [--type <lighting|media>] [--room-id <id>] [--json|--yaml]",
            "  crestron-cli audio <target> {on|off|set|mute|unmute|toggle|source} [value] [--player <A|B>] [--json|--yaml]",
            "  crestron-cli audio <A|B>=<source-id|source-name>",
            "  crestron-cli light <target> {on|off|set|toggle} [value] [--json|--yaml]",
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
    if command == "speaker":
        return _speaker_command(argv[1:])
    if command == "audio":
        return _audio_command(argv[1:])
    if command == "light":
        return _action_command(argv[1:])

    return _emit_error(f"unknown command '{command}'", details="run 'crestron-cli --help' for usage")


def cli() -> None:
    raise SystemExit(main())


if __name__ == "__main__":
    raise SystemExit(main())
