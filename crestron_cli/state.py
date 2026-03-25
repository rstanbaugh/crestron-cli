from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    import yaml
except Exception:
    yaml = None

from .utils import normalize_name, raw_to_percent, utc_now_iso


STATE_PATH = Path("~/.openclaw/tools/crestron/state.yaml").expanduser()


class StateError(Exception):
    pass


def _empty_maps() -> Dict[str, Dict[str, Any]]:
    return {"by_id": {}, "by_name_normalized": {}}


def default_state(base_url: str = "") -> Dict[str, Any]:
    return {
        "version": 1,
        "last_refresh": None,
        "base_url": base_url,
        "auth": {"authkey": None, "expires_approx": None},
        "rooms": _empty_maps(),
        "lights": _empty_maps(),
        "scenes": _empty_maps(),
        "speakers": _empty_maps(),
        "speaker_presets": {"by_room_id": {}},
        "quickactions": {},
        "metadata": {
            "server_firmware": None,
            "last_successful_init": None,
            "refresh_count": 0,
        },
    }


def load_state(path: Path = STATE_PATH) -> Dict[str, Any]:
    if yaml is None:
        raise StateError("missing dependency: install pyyaml")
    if not path.exists():
        return default_state()
    try:
        loaded = yaml.safe_load(path.read_text())
    except Exception as exc:
        raise StateError(f"failed to read state file: {exc}")

    if not isinstance(loaded, dict):
        return default_state()

    merged = default_state(base_url=str(loaded.get("base_url") or ""))
    merged.update(loaded)

    for key in ["rooms", "lights", "scenes", "speakers"]:
        container = merged.get(key)
        if not isinstance(container, dict):
            merged[key] = _empty_maps()
            continue
        if not isinstance(container.get("by_id"), dict):
            container["by_id"] = {}
        if not isinstance(container.get("by_name_normalized"), dict):
            container["by_name_normalized"] = {}

    if not isinstance(merged.get("metadata"), dict):
        merged["metadata"] = default_state().get("metadata")

    speaker_presets = merged.get("speaker_presets")
    if not isinstance(speaker_presets, dict):
        merged["speaker_presets"] = {"by_room_id": {}}
    elif not isinstance(speaker_presets.get("by_room_id"), dict):
        speaker_presets["by_room_id"] = {}

    return merged


def save_state(state: Dict[str, Any], path: Path = STATE_PATH) -> None:
    if yaml is None:
        raise StateError("missing dependency: install pyyaml")
    path.parent.mkdir(parents=True, exist_ok=True)
    text = yaml.safe_dump(state, sort_keys=False)
    path.write_text(text)


def _build_room_maps(rooms: List[Dict[str, Any]]) -> Dict[str, Any]:
    by_id: Dict[str, Dict[str, Any]] = {}
    by_name: Dict[str, int] = {}

    for room in rooms:
        room_id = room.get("id")
        room_name = room.get("name")
        if room_id is None:
            continue
        try:
            room_id_int = int(room_id)
        except Exception:
            continue

        record = {"id": room_id_int, "name": str(room_name or f"Room {room_id_int}")}
        by_id[str(room_id_int)] = record

        normalized = normalize_name(record["name"])
        if normalized:
            by_name[normalized] = room_id_int

    return {"by_id": by_id, "by_name_normalized": by_name}


def _build_light_maps(lights: List[Dict[str, Any]]) -> Dict[str, Any]:
    by_id: Dict[str, Dict[str, Any]] = {}
    by_name: Dict[str, List[int]] = {}

    for light in lights:
        light_id = light.get("id")
        if light_id is None:
            continue
        try:
            light_id_int = int(light_id)
        except Exception:
            continue

        current_level = light.get("current_level")
        try:
            current_level_int = int(current_level) if current_level is not None else None
        except Exception:
            current_level_int = None

        record = {
            "id": light_id_int,
            "name": str(light.get("name") or f"Light {light_id_int}"),
            "room_id": light.get("room_id"),
            "current_level": current_level_int,
            "percent": raw_to_percent(current_level_int),
            "subtype": light.get("subtype"),
        }
        by_id[str(light_id_int)] = record

        normalized = normalize_name(record["name"])
        if normalized:
            existing = by_name.get(normalized)
            if isinstance(existing, list):
                existing.append(light_id_int)
            else:
                by_name[normalized] = [light_id_int]

    return {"by_id": by_id, "by_name_normalized": by_name}


def _build_scene_maps(scenes: List[Dict[str, Any]]) -> Dict[str, Any]:
    by_id: Dict[str, Dict[str, Any]] = {}
    by_name: Dict[str, List[int]] = {}

    for scene in scenes:
        scene_id = scene.get("id")
        if scene_id is None:
            continue
        try:
            scene_id_int = int(scene_id)
        except Exception:
            continue

        record = {
            "id": scene_id_int,
            "name": str(scene.get("name") or f"Scene {scene_id_int}"),
            "room_id": scene.get("room_id"),
            "scene_type": scene.get("scene_type"),
            "status": scene.get("status"),
        }
        by_id[str(scene_id_int)] = record

        normalized = normalize_name(record["name"])
        if normalized:
            existing = by_name.get(normalized)
            if isinstance(existing, list):
                existing.append(scene_id_int)
            else:
                by_name[normalized] = [scene_id_int]

    return {"by_id": by_id, "by_name_normalized": by_name}


def _build_speaker_maps(speakers: List[Dict[str, Any]]) -> Dict[str, Any]:
    by_id: Dict[str, Dict[str, Any]] = {}
    by_name: Dict[str, int] = {}

    for speaker in speakers:
        speaker_id = speaker.get("id")
        if speaker_id is None:
            continue
        try:
            speaker_id_int = int(speaker_id)
        except Exception:
            continue

        room_id = speaker.get("room_id")
        try:
            room_id_int = int(room_id) if room_id is not None else None
        except Exception:
            room_id_int = None

        current_volume_percent = speaker.get("current_volume_percent")
        try:
            current_volume_percent = int(round(float(current_volume_percent))) if current_volume_percent is not None else None
            if current_volume_percent is not None and current_volume_percent > 100:
                normalized = raw_to_percent(current_volume_percent)
                current_volume_percent = int(round(normalized)) if normalized is not None else None
        except Exception:
            current_volume_percent = None

        current_source_id = speaker.get("current_source_id")
        try:
            current_source_id = int(current_source_id) if current_source_id is not None else None
        except Exception:
            current_source_id = None

        available_sources: List[Dict[str, Any]] = []
        for src in speaker.get("available_sources") or []:
            if not isinstance(src, dict):
                continue
            src_id = src.get("id")
            try:
                src_id_int = int(src_id) if src_id is not None else None
            except Exception:
                src_id_int = None
            available_sources.append({"id": src_id_int, "source_name": str(src.get("source_name") or "")})

        record = {
            "id": speaker_id_int,
            "name": str(speaker.get("name") or f"Speaker {speaker_id_int}"),
            "room_id": room_id_int,
            "current_volume_percent": current_volume_percent,
            "current_mute_state": str(speaker.get("current_mute_state") or "").lower() or None,
            "current_power_state": str(speaker.get("current_power_state") or "").lower() or None,
            "current_source_id": current_source_id,
            "available_sources": available_sources,
            "available_volume_controls": list(speaker.get("available_volume_controls") or []),
            "available_mute_controls": list(speaker.get("available_mute_controls") or []),
        }
        by_id[str(speaker_id_int)] = record

        normalized = normalize_name(record["name"])
        if normalized:
            by_name[normalized] = speaker_id_int

    return {"by_id": by_id, "by_name_normalized": by_name}


def build_state(
    *,
    base_url: str,
    authkey: Optional[str],
    rooms: List[Dict[str, Any]],
    lights: List[Dict[str, Any]],
    scenes: List[Dict[str, Any]],
    speakers: Optional[List[Dict[str, Any]]] = None,
    previous_state: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    previous_state = previous_state or default_state(base_url=base_url)
    metadata = previous_state.get("metadata") if isinstance(previous_state.get("metadata"), dict) else {}
    refresh_count = metadata.get("refresh_count") if isinstance(metadata, dict) else 0
    try:
        refresh_count = int(refresh_count)
    except Exception:
        refresh_count = 0

    return {
        "version": 1,
        "last_refresh": utc_now_iso(),
        "base_url": base_url,
        "auth": {
            "authkey": authkey,
            "expires_approx": None,
        },
        "rooms": _build_room_maps(rooms),
        "lights": _build_light_maps(lights),
        "scenes": _build_scene_maps(scenes),
        "speakers": _build_speaker_maps(speakers or []),
        "speaker_presets": {
            "by_room_id": dict((((previous_state.get("speaker_presets") or {}).get("by_room_id") or {}))),
        },
        "quickactions": {},
        "metadata": {
            "server_firmware": metadata.get("server_firmware") if isinstance(metadata, dict) else None,
            "last_successful_init": utc_now_iso(),
            "refresh_count": refresh_count + 1,
        },
    }


def has_cached_inventory(state: Dict[str, Any]) -> bool:
    lights = ((state.get("lights") or {}).get("by_id") or {})
    scenes = ((state.get("scenes") or {}).get("by_id") or {})
    speakers = ((state.get("speakers") or {}).get("by_id") or {})
    rooms = ((state.get("rooms") or {}).get("by_id") or {})
    return bool(lights or scenes or speakers or rooms)


def room_name_for_id(state: Dict[str, Any], room_id: Any) -> Optional[str]:
    if room_id is None:
        return None
    room = ((state.get("rooms") or {}).get("by_id") or {}).get(str(room_id))
    if isinstance(room, dict):
        name = room.get("name")
        if name:
            return str(name)
    return None


def resolve_room_target(state: Dict[str, Any], target: str) -> int:
    rooms = (state.get("rooms") or {}).get("by_id") or {}
    name_map = (state.get("rooms") or {}).get("by_name_normalized") or {}

    stripped = target.strip()
    if stripped.isdigit():
        room_id = int(stripped)
        if not isinstance(rooms.get(str(room_id)), dict):
            raise StateError(f"unknown room id {target}")
        return room_id

    normalized = normalize_name(stripped)
    room_id = name_map.get(normalized)
    if room_id is None:
        raise StateError(f"unknown room target '{target}'")

    if not isinstance(rooms.get(str(room_id)), dict):
        raise StateError(f"room id {room_id} is missing from cache")
    return int(room_id)


def resolve_light_target(state: Dict[str, Any], target: str) -> Tuple[int, Dict[str, Any]]:
    lights = (state.get("lights") or {}).get("by_id") or {}
    name_map = (state.get("lights") or {}).get("by_name_normalized") or {}

    if target.strip().isdigit():
        key = str(int(target.strip()))
        light = lights.get(key)
        if not isinstance(light, dict):
            raise StateError(f"unknown light id {target}")
        return int(key), light

    normalized = normalize_name(target)
    light_ids = name_map.get(normalized)
    if light_ids is None:
        raise StateError(f"unknown light target '{target}'")

    if isinstance(light_ids, int):
        light_ids = [light_ids]
    if not isinstance(light_ids, list) or not light_ids:
        raise StateError(f"unknown light target '{target}'")

    candidate_ids: List[int] = []
    for value in light_ids:
        try:
            candidate_ids.append(int(value))
        except Exception:
            continue

    if not candidate_ids:
        raise StateError(f"unknown light target '{target}'")

    if len(candidate_ids) > 1:
        ids_text = ", ".join(str(light_id) for light_id in sorted(candidate_ids))
        raise StateError(f"ambiguous light target '{target}'; use id=... (matches: {ids_text})")

    light_id = candidate_ids[0]
    light = lights.get(str(light_id))
    if not isinstance(light, dict):
        raise StateError(f"light id {light_id} is missing from cache")

    return int(light_id), light


def update_light_level(state: Dict[str, Any], light_id: int, level_raw: int) -> Dict[str, Any]:
    lights = (state.get("lights") or {}).get("by_id") or {}
    record = lights.get(str(light_id))
    if not isinstance(record, dict):
        return state

    record["current_level"] = int(level_raw)
    record["percent"] = raw_to_percent(level_raw)
    return state


def list_lights(state: Dict[str, Any], room_id: int | None = None) -> List[Dict[str, Any]]:
    lights = (state.get("lights") or {}).get("by_id") or {}
    out: List[Dict[str, Any]] = []
    for key, item in lights.items():
        if not isinstance(item, dict):
            continue
        row = dict(item)
        row["id"] = int(item.get("id", key))
        if room_id is not None:
            try:
                row_room_id = int(row.get("room_id"))
            except Exception:
                continue
            if row_room_id != room_id:
                continue
            row["room_id"] = row_room_id
        room_name = room_name_for_id(state, row.get("room_id"))
        if room_name:
            row["room_name"] = room_name
        out.append(row)
    out.sort(
        key=lambda entry: (
            0 if entry.get("room_name") else 1,
            str(entry.get("room_name") or "").lower(),
            str(entry.get("name") or "").lower(),
            int(entry.get("id") or 0),
        )
    )
    return out


def list_rooms(state: Dict[str, Any]) -> List[Dict[str, Any]]:
    rooms = (state.get("rooms") or {}).get("by_id") or {}
    out: List[Dict[str, Any]] = []
    for key, item in rooms.items():
        if not isinstance(item, dict):
            continue
        row = dict(item)
        row["id"] = int(item.get("id", key))
        out.append(row)
    out.sort(key=lambda entry: (str(entry.get("name") or "").lower(), int(entry.get("id") or 0)))
    return out


def list_scenes(state: Dict[str, Any], room_id: int | None = None) -> List[Dict[str, Any]]:
    scenes = (state.get("scenes") or {}).get("by_id") or {}
    out: List[Dict[str, Any]] = []
    for key, item in scenes.items():
        if not isinstance(item, dict):
            continue
        row = dict(item)
        row["id"] = int(item.get("id", key))
        if "scene_type" not in row:
            row["scene_type"] = None
        if "status" not in row:
            row["status"] = None
        if room_id is not None:
            try:
                row_room_id = int(row.get("room_id"))
            except Exception:
                continue
            if row_room_id != room_id:
                continue
            row["room_id"] = row_room_id
        room_name = room_name_for_id(state, row.get("room_id"))
        if room_name:
            row["room_name"] = room_name
        out.append(row)
    out.sort(
        key=lambda entry: (
            0 if entry.get("room_name") else 1,
            str(entry.get("room_name") or "").lower(),
            str(entry.get("name") or "").lower(),
            int(entry.get("id") or 0),
        )
    )
    return out


def list_speakers(state: Dict[str, Any], room_id: int | None = None) -> List[Dict[str, Any]]:
    speakers = (state.get("speakers") or {}).get("by_id") or {}
    out: List[Dict[str, Any]] = []
    for key, item in speakers.items():
        if not isinstance(item, dict):
            continue
        row = dict(item)
        row["id"] = int(item.get("id", key))
        if room_id is not None:
            try:
                row_room_id = int(row.get("room_id"))
            except Exception:
                continue
            if row_room_id != room_id:
                continue
            row["room_id"] = row_room_id
        room_name = room_name_for_id(state, row.get("room_id"))
        if room_name:
            row["room_name"] = room_name
        out.append(row)
    out.sort(
        key=lambda entry: (
            0 if entry.get("room_name") else 1,
            str(entry.get("room_name") or "").lower(),
            str(entry.get("name") or "").lower(),
            int(entry.get("id") or 0),
        )
    )
    return out


def resolve_speaker_target(state: Dict[str, Any], target: str) -> Tuple[int, Dict[str, Any]]:
    speakers = (state.get("speakers") or {}).get("by_id") or {}
    name_map = (state.get("speakers") or {}).get("by_name_normalized") or {}
    room_name_map = (state.get("rooms") or {}).get("by_name_normalized") or {}

    stripped = target.strip()
    if stripped.isdigit():
        key = str(int(stripped))
        speaker = speakers.get(key)
        if isinstance(speaker, dict):
            return int(key), speaker
        # If the id does not match a speaker id, try room id fallback.
        room_id = int(key)
        matches = [entry for entry in speakers.values() if isinstance(entry, dict) and int(entry.get("room_id") or -1) == room_id]
        if len(matches) == 1:
            speaker = matches[0]
            return int(speaker.get("id")), speaker
        if len(matches) > 1:
            raise StateError(f"multiple speakers found for room id {room_id}; use speaker id")
        raise StateError(f"unknown speaker target '{target}'")

    normalized = normalize_name(target)
    speaker_id = name_map.get(normalized)
    if speaker_id is not None:
        speaker = speakers.get(str(speaker_id))
        if isinstance(speaker, dict):
            return int(speaker_id), speaker

    room_id = room_name_map.get(normalized)
    if room_id is not None:
        matches = [entry for entry in speakers.values() if isinstance(entry, dict) and int(entry.get("room_id") or -1) == int(room_id)]
        if len(matches) == 1:
            speaker = matches[0]
            return int(speaker.get("id")), speaker
        if len(matches) > 1:
            raise StateError(f"multiple speakers found for room '{target}'; use speaker id")

    raise StateError(f"unknown speaker target '{target}'")


def resolve_speaker_source_target(
    speaker: Dict[str, Any],
    source_target: str | None,
    *,
    player: str | None = None,
    preferred_source_id: int | None = None,
) -> Tuple[int, str]:
    available_sources = [src for src in (speaker.get("available_sources") or []) if isinstance(src, dict)]
    if not available_sources:
        raise StateError("speaker has no available sources")

    normalized_target = normalize_name(source_target) if source_target else ""
    normalized_player = normalize_name(player) if player else ""

    def _matches_player(src_name: str) -> bool:
        if not normalized_player:
            return True
        return normalize_name(src_name).startswith(f"player {normalized_player}")

    if preferred_source_id is not None:
        for src in available_sources:
            src_name = str(src.get("source_name") or "")
            if int(src.get("id") or -1) == int(preferred_source_id) and _matches_player(src_name):
                return int(src.get("id")), src_name

    # Explicit source id.
    if source_target and source_target.strip().isdigit():
        wanted_id = int(source_target.strip())
        for src in available_sources:
            if int(src.get("id") or -1) == wanted_id and _matches_player(str(src.get("source_name") or "")):
                return int(src.get("id")), str(src.get("source_name") or "")
        raise StateError(f"unknown source id {wanted_id} for this speaker")

    # Explicit source name.
    if normalized_target:
        for src in available_sources:
            src_name = str(src.get("source_name") or "")
            if normalize_name(src_name) == normalized_target and _matches_player(src_name):
                return int(src.get("id")), src_name
        raise StateError(f"unknown source '{source_target}' for this speaker")

    # Player-only default source pick.
    if normalized_player:
        for src in available_sources:
            src_name = str(src.get("source_name") or "")
            if _matches_player(src_name):
                return int(src.get("id")), src_name

    # Fallback first source.
    first = available_sources[0]
    return int(first.get("id")), str(first.get("source_name") or "")


def update_speaker_state(
    state: Dict[str, Any],
    speaker_id: int,
    *,
    power_state: str | None = None,
    mute_state: str | None = None,
    volume_percent: int | None = None,
    source_id: int | None = None,
) -> Dict[str, Any]:
    speakers = (state.get("speakers") or {}).get("by_id") or {}
    record = speakers.get(str(speaker_id))
    if not isinstance(record, dict):
        return state

    if power_state is not None:
        record["current_power_state"] = str(power_state).lower()
    if mute_state is not None:
        record["current_mute_state"] = str(mute_state).lower()
    if volume_percent is not None:
        record["current_volume_percent"] = int(round(float(volume_percent)))
    if source_id is not None:
        record["current_source_id"] = int(source_id)
    return state


def get_speaker_player_default(state: Dict[str, Any], room_id: int, player: str) -> int | None:
    by_room = ((state.get("speaker_presets") or {}).get("by_room_id") or {})
    room_entry = by_room.get(str(int(room_id)))
    if not isinstance(room_entry, dict):
        return None
    value = room_entry.get(player.upper())
    try:
        return int(value) if value is not None else None
    except Exception:
        return None


def set_speaker_player_default(state: Dict[str, Any], room_id: int, player: str, source_id: int) -> Dict[str, Any]:
    presets = state.setdefault("speaker_presets", {})
    by_room = presets.setdefault("by_room_id", {})
    room_key = str(int(room_id))
    room_entry = by_room.get(room_key)
    if not isinstance(room_entry, dict):
        room_entry = {}
        by_room[room_key] = room_entry
    room_entry[player.upper()] = int(source_id)
    return state


def resolve_scene_target(
    state: Dict[str, Any],
    target: str,
    *,
    scene_type: str | None = None,
    room_id: int | None = None,
) -> Tuple[int, Dict[str, Any]]:
    scenes = (state.get("scenes") or {}).get("by_id") or {}

    if target.strip().isdigit():
        key = str(int(target.strip()))
        scene = scenes.get(key)
        if not isinstance(scene, dict):
            raise StateError(f"unknown scene id {target}")
        return int(key), scene

    normalized_target = normalize_name(target)
    wanted_type = normalize_name(scene_type) if scene_type else None

    matches: List[Tuple[int, Dict[str, Any]]] = []
    for key, raw in scenes.items():
        if not isinstance(raw, dict):
            continue
        try:
            scene_id = int(raw.get("id", key))
        except Exception:
            continue

        if normalize_name(raw.get("name")) != normalized_target:
            continue

        if room_id is not None:
            try:
                entry_room_id = int(raw.get("room_id"))
            except Exception:
                continue
            if entry_room_id != room_id:
                continue

        if wanted_type:
            entry_type = normalize_name(raw.get("scene_type"))
            if entry_type != wanted_type:
                continue

        matches.append((scene_id, raw))

    if not matches:
        raise StateError(f"unknown scene target '{target}'")

    if len(matches) > 1:
        summary = ", ".join(
            [
                f"id={scene_id},type={entry.get('scene_type') or 'unknown'},room_id={entry.get('room_id')}"
                for scene_id, entry in matches[:6]
            ]
        )
        raise StateError(
            "ambiguous scene target; provide scene id, --type, or --room-id"
            + (f" (matches: {summary})" if summary else "")
        )

    return matches[0]
