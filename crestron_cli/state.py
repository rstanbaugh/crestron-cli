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

    for key in ["rooms", "lights", "scenes"]:
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
    by_name: Dict[str, int] = {}

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
            by_name[normalized] = light_id_int

    return {"by_id": by_id, "by_name_normalized": by_name}


def _build_scene_maps(scenes: List[Dict[str, Any]]) -> Dict[str, Any]:
    by_id: Dict[str, Dict[str, Any]] = {}
    by_name: Dict[str, int] = {}

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
        }
        by_id[str(scene_id_int)] = record

        normalized = normalize_name(record["name"])
        if normalized:
            by_name[normalized] = scene_id_int

    return {"by_id": by_id, "by_name_normalized": by_name}


def build_state(
    *,
    base_url: str,
    authkey: Optional[str],
    rooms: List[Dict[str, Any]],
    lights: List[Dict[str, Any]],
    scenes: List[Dict[str, Any]],
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
    rooms = ((state.get("rooms") or {}).get("by_id") or {})
    return bool(lights or scenes or rooms)


def room_name_for_id(state: Dict[str, Any], room_id: Any) -> Optional[str]:
    if room_id is None:
        return None
    room = ((state.get("rooms") or {}).get("by_id") or {}).get(str(room_id))
    if isinstance(room, dict):
        name = room.get("name")
        if name:
            return str(name)
    return None


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
    light_id = name_map.get(normalized)
    if light_id is None:
        raise StateError(f"unknown light target '{target}'")

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


def list_lights(state: Dict[str, Any]) -> List[Dict[str, Any]]:
    lights = (state.get("lights") or {}).get("by_id") or {}
    out: List[Dict[str, Any]] = []
    for key, item in lights.items():
        if not isinstance(item, dict):
            continue
        row = dict(item)
        row["id"] = int(item.get("id", key))
        room_name = room_name_for_id(state, row.get("room_id"))
        if room_name:
            row["room_name"] = room_name
        out.append(row)
    out.sort(key=lambda entry: (str(entry.get("name") or "").lower(), int(entry.get("id") or 0)))
    return out


def list_scenes(state: Dict[str, Any]) -> List[Dict[str, Any]]:
    scenes = (state.get("scenes") or {}).get("by_id") or {}
    out: List[Dict[str, Any]] = []
    for key, item in scenes.items():
        if not isinstance(item, dict):
            continue
        row = dict(item)
        row["id"] = int(item.get("id", key))
        room_name = room_name_for_id(state, row.get("room_id"))
        if room_name:
            row["room_name"] = room_name
        out.append(row)
    out.sort(key=lambda entry: (str(entry.get("name") or "").lower(), int(entry.get("id") or 0)))
    return out
