from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

try:
    import requests
except Exception:
    requests = None

from .config import Config
from .utils import raw_to_percent


ERROR_SOURCE_MAP = {
    5001: "Session expired",
    5002: "Authentication failed",
    7003: "Lights operation error",
    7006: "Scenes operation error",
    8010: "Media rooms operation error",
}


@dataclass
class CrestronApiError(Exception):
    message: str
    details: Optional[str] = None
    error_source: Optional[int] = None
    status_code: Optional[int] = None

    def __str__(self) -> str:
        if self.details:
            return f"{self.message}: {self.details}"
        return self.message


class CrestronClient:
    def __init__(self, config: Config):
        self.config = config
        self.session = requests.Session() if requests is not None else None
        self.authkey: Optional[str] = None
        self.reauth_happened = False

    @staticmethod
    def _pick(obj: Dict[str, Any], *keys: str) -> Any:
        for key in keys:
            if key in obj:
                return obj[key]
        return None

    @staticmethod
    def _extract_error_source(data: Any) -> Optional[int]:
        if not isinstance(data, dict):
            return None
        value = data.get("errorSource")
        if value is None:
            value = data.get("ErrorSource")
        if value is None:
            value = data.get("error_source")
        if value is None:
            return None
        try:
            return int(value)
        except Exception:
            return None

    @staticmethod
    def _extract_items(data: Any, keys: List[str]) -> List[Dict[str, Any]]:
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        if not isinstance(data, dict):
            return []

        for key in keys:
            value = data.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]

        nested = data.get("data")
        if isinstance(nested, list):
            return [item for item in nested if isinstance(item, dict)]

        if isinstance(nested, dict):
            for key in keys:
                value = nested.get(key)
                if isinstance(value, list):
                    return [item for item in value if isinstance(item, dict)]

        return []

    @staticmethod
    def _extract_authkey(data: Any) -> Optional[str]:
        if not isinstance(data, dict):
            return None

        for key in ["authkey", "authKey", "AuthKey", "Authkey"]:
            value = data.get(key)
            if value:
                return str(value)

        nested = data.get("data")
        if isinstance(nested, dict):
            for key in ["authkey", "authKey", "AuthKey", "Authkey"]:
                value = nested.get(key)
                if value:
                    return str(value)

        return None

    def _headers(self, include_authkey: bool) -> Dict[str, str]:
        headers = {
            "Accept": "application/json",
            "Crestron-RestAPI-AuthToken": self.config.auth_token,
        }
        if include_authkey and self.authkey:
            headers["Crestron-RestAPI-Authkey"] = self.authkey
        return headers

    def _request_via_curl(
        self,
        method: str,
        path: str,
        *,
        json_body: Optional[Dict[str, Any]],
        include_authkey: bool,
    ) -> Any:
        url = f"{self.config.base_url}{path}"
        headers = self._headers(include_authkey=include_authkey)

        cmd: List[str] = [
            "curl",
            "-sS",
            "-X",
            method.upper(),
            url,
            "--connect-timeout",
            str(max(1, int(self.config.timeout_s))),
            "--max-time",
            str(max(1, int(self.config.timeout_s))),
        ]

        for key, value in headers.items():
            cmd.extend(["-H", f"{key}: {value}"])

        if json_body is not None:
            cmd.extend(["-H", "Content-Type: application/json", "--data", json.dumps(json_body)])

        cmd.extend(["-w", "\n__STATUS__:%{http_code}"])

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        except Exception as exc:
            raise CrestronApiError("request failed", details=f"curl execution failed: {exc}")

        if result.returncode != 0:
            details = result.stderr.strip() or result.stdout.strip() or f"curl exit {result.returncode}"
            raise CrestronApiError("request failed", details=details)

        marker = "\n__STATUS__:"
        if marker not in result.stdout:
            raise CrestronApiError("request failed", details="curl response parse error")

        body_text, _, status_text = result.stdout.rpartition(marker)
        try:
            status_code = int(status_text.strip())
        except Exception:
            raise CrestronApiError("request failed", details="invalid HTTP status from curl")

        parsed: Any = None
        if body_text.strip():
            try:
                parsed = json.loads(body_text)
            except Exception:
                parsed = None

        if status_code >= 400:
            if isinstance(parsed, dict):
                detail = self._pick(parsed, "message", "Message", "error", "Error", "details", "Details")
                error_source = self._extract_error_source(parsed)
            else:
                detail = (body_text or "").strip()[:300] or None
                error_source = None

            mapped = ERROR_SOURCE_MAP.get(error_source)
            message = mapped or f"HTTP {status_code}"
            raise CrestronApiError(message, details=str(detail) if detail else None, error_source=error_source, status_code=status_code)

        if isinstance(parsed, dict):
            error_source = self._extract_error_source(parsed)
            if error_source not in (None, 0):
                mapped = ERROR_SOURCE_MAP.get(error_source, "API operation failed")
                detail = self._pick(parsed, "message", "Message", "error", "Error", "details", "Details")
                raise CrestronApiError(mapped, details=str(detail) if detail else None, error_source=error_source)

        return parsed if parsed is not None else {}

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: Optional[Dict[str, Any]] = None,
        include_authkey: bool,
        retry_on_auth: bool = True,
    ) -> Any:
        if self.session is None:
            return self._request_via_curl(
                method,
                path,
                json_body=json_body,
                include_authkey=include_authkey,
            )

        url = f"{self.config.base_url}{path}"
        headers = self._headers(include_authkey=include_authkey)
        if json_body is not None:
            headers["Content-Type"] = "application/json"

        try:
            response = self.session.request(
                method=method,
                url=url,
                headers=headers,
                json=json_body,
                timeout=self.config.timeout_s,
            )
        except requests.Timeout:
            raise CrestronApiError("request timed out", details=f"after {self.config.timeout_s:.1f}s")
        except requests.RequestException as exc:
            return self._request_via_curl(
                method,
                path,
                json_body=json_body,
                include_authkey=include_authkey,
            )

        parsed: Any = None
        body_text = response.text.strip()
        if body_text:
            try:
                parsed = response.json()
            except Exception:
                parsed = None

        if response.status_code >= 400:
            detail = None
            if isinstance(parsed, dict):
                detail = self._pick(parsed, "message", "Message", "error", "Error", "details", "Details")
                error_source = self._extract_error_source(parsed)
            else:
                error_source = None
                detail = body_text[:300] if body_text else None

            if error_source in (5001, 5002) and retry_on_auth and include_authkey:
                self.login(force=True)
                self.reauth_happened = True
                return self._request(
                    method,
                    path,
                    json_body=json_body,
                    include_authkey=include_authkey,
                    retry_on_auth=False,
                )

            mapped = ERROR_SOURCE_MAP.get(error_source)
            message = mapped or f"HTTP {response.status_code}"
            raise CrestronApiError(message, details=detail, error_source=error_source, status_code=response.status_code)

        if isinstance(parsed, dict):
            error_source = self._extract_error_source(parsed)
            if error_source in (5001, 5002) and retry_on_auth and include_authkey:
                self.login(force=True)
                self.reauth_happened = True
                return self._request(
                    method,
                    path,
                    json_body=json_body,
                    include_authkey=include_authkey,
                    retry_on_auth=False,
                )
            if error_source not in (None, 0):
                mapped = ERROR_SOURCE_MAP.get(error_source, "API operation failed")
                detail = self._pick(parsed, "message", "Message", "error", "Error", "details", "Details")
                raise CrestronApiError(mapped, details=str(detail) if detail else None, error_source=error_source)

        return parsed if parsed is not None else {}

    def login(self, *, force: bool = False) -> str:
        if self.authkey and not force:
            return self.authkey

        response = self._request("GET", "/login", include_authkey=False, retry_on_auth=False)
        authkey = self._extract_authkey(response)
        if not authkey:
            raise CrestronApiError("authentication failed", details="login response did not include authkey")

        self.authkey = authkey
        return authkey

    def ensure_login(self) -> str:
        return self.login(force=False)

    def get_rooms(self) -> List[Dict[str, Any]]:
        self.ensure_login()
        data = self._request("GET", "/rooms", include_authkey=True)
        out: List[Dict[str, Any]] = []
        for item in self._extract_items(data, ["rooms", "Rooms"]):
            room_id = self._pick(item, "id", "Id", "roomId", "RoomId")
            room_name = self._pick(item, "name", "Name", "roomName", "RoomName")
            if room_id is None:
                continue
            try:
                room_id = int(room_id)
            except Exception:
                continue
            out.append({"id": room_id, "name": str(room_name or f"Room {room_id}")})
        return out

    def get_lights(self) -> List[Dict[str, Any]]:
        self.ensure_login()
        data = self._request("GET", "/lights", include_authkey=True)
        out: List[Dict[str, Any]] = []
        for item in self._extract_items(data, ["lights", "Lights", "devices", "Devices"]):
            light_id = self._pick(item, "id", "Id", "lightId", "LightId", "deviceId", "DeviceId")
            if light_id is None:
                continue
            try:
                light_id = int(light_id)
            except Exception:
                continue

            room_id = self._pick(item, "roomId", "RoomId", "room_id")
            try:
                room_id = int(room_id) if room_id is not None else None
            except Exception:
                room_id = None

            current_level = self._pick(item, "current_level", "CurrentLevel", "level", "Level", "value", "Value")
            try:
                current_level = int(current_level) if current_level is not None else None
            except Exception:
                current_level = None

            subtype = self._pick(item, "subtype", "SubType", "type", "Type")
            name = self._pick(item, "name", "Name", "lightName", "LightName", "deviceName", "DeviceName")

            out.append(
                {
                    "id": light_id,
                    "name": str(name or f"Light {light_id}"),
                    "room_id": room_id,
                    "current_level": current_level,
                    "subtype": str(subtype) if subtype is not None else None,
                }
            )
        return out

    def get_scenes(self) -> List[Dict[str, Any]]:
        self.ensure_login()
        data = self._request("GET", "/scenes", include_authkey=True)
        out: List[Dict[str, Any]] = []
        for item in self._extract_items(data, ["scenes", "Scenes"]):
            scene_id = self._pick(item, "id", "Id", "sceneId", "SceneId")
            if scene_id is None:
                continue
            try:
                scene_id = int(scene_id)
            except Exception:
                continue

            room_id = self._pick(item, "roomId", "RoomId", "room_id")
            try:
                room_id = int(room_id) if room_id is not None else None
            except Exception:
                room_id = None

            name = self._pick(item, "name", "Name", "sceneName", "SceneName")
            scene_type = self._pick(item, "type", "Type", "sceneType", "SceneType")
            status = self._pick(item, "status", "Status")
            out.append(
                {
                    "id": scene_id,
                    "name": str(name or f"Scene {scene_id}"),
                    "room_id": room_id,
                    "scene_type": str(scene_type).strip().lower() if scene_type is not None else None,
                    "status": status,
                }
            )
        return out

    def recall_scene(self, scene_id: int) -> Any:
        self.ensure_login()
        paths = [f"/scenes/recall/{int(scene_id)}", f"/scenes/Recall/{int(scene_id)}"]

        last_error: Optional[CrestronApiError] = None
        for path in paths:
            try:
                return self._request(
                    "POST",
                    path,
                    json_body={},
                    include_authkey=True,
                )
            except CrestronApiError as exc:
                last_error = exc
                continue

        if last_error:
            raise last_error
        raise CrestronApiError("scenes operation failed", details="unknown recall failure")

    def get_speakers(self) -> List[Dict[str, Any]]:
        self.ensure_login()
        data = self._request("GET", "/mediarooms", include_authkey=True)
        out: List[Dict[str, Any]] = []
        for item in self._extract_items(data, ["mediaRooms", "MediaRooms", "mediarooms", "media_rooms"]):
            speaker_id = self._pick(item, "id", "Id", "mediaRoomId", "MediaRoomId")
            if speaker_id is None:
                continue
            try:
                speaker_id = int(speaker_id)
            except Exception:
                continue

            room_id = self._pick(item, "roomId", "RoomId", "room_id")
            try:
                room_id = int(room_id) if room_id is not None else None
            except Exception:
                room_id = None

            current_volume = self._pick(item, "currentVolumeLevel", "CurrentVolumeLevel", "volume", "Volume")
            try:
                current_volume_percent = int(round(float(current_volume))) if current_volume is not None else None
                if current_volume_percent is not None and current_volume_percent > 100:
                    normalized = raw_to_percent(current_volume_percent)
                    current_volume_percent = int(round(normalized)) if normalized is not None else None
            except Exception:
                current_volume_percent = None

            current_source_id = self._pick(item, "currentSourceId", "CurrentSourceId", "currentProviderId", "CurrentProviderId")
            try:
                current_source_id = int(current_source_id) if current_source_id is not None else None
            except Exception:
                current_source_id = None

            available_sources_raw = self._pick(item, "availableSources", "AvailableSources")
            if available_sources_raw is None:
                providers = self._pick(item, "availableProviders", "AvailableProviders")
                available_sources_raw = providers if isinstance(providers, list) else []

            available_sources: List[Dict[str, Any]] = []
            if isinstance(available_sources_raw, list):
                for index, source in enumerate(available_sources_raw):
                    if isinstance(source, dict):
                        src_id = self._pick(source, "id", "Id", "sourceId", "SourceId", "providerId", "ProviderId")
                        src_name = self._pick(source, "sourceName", "SourceName", "name", "Name", "providerName", "ProviderName")
                    else:
                        src_id = index
                        src_name = str(source)
                    try:
                        src_id = int(src_id) if src_id is not None else None
                    except Exception:
                        src_id = None
                    available_sources.append({"id": src_id, "source_name": str(src_name or "")})

            current_mute_state = self._pick(item, "currentMuteState", "CurrentMuteState", "mute", "Mute")
            current_power_state = self._pick(item, "currentPowerState", "CurrentPowerState", "power", "Power")
            available_volume_controls = self._pick(item, "availableVolumeControls", "AvailableVolumeControls")
            available_mute_controls = self._pick(item, "availableMuteControls", "AvailableMuteControls")
            name = self._pick(item, "name", "Name", "mediaRoomName", "MediaRoomName")

            out.append(
                {
                    "id": speaker_id,
                    "name": str(name or f"Speaker {speaker_id}"),
                    "room_id": room_id,
                    "current_volume_percent": current_volume_percent,
                    "current_mute_state": str(current_mute_state).lower() if current_mute_state is not None else None,
                    "current_power_state": str(current_power_state).lower() if current_power_state is not None else None,
                    "current_source_id": current_source_id,
                    "available_sources": available_sources,
                    "available_volume_controls": list(available_volume_controls or []),
                    "available_mute_controls": list(available_mute_controls or []),
                }
            )
        return out

    def _post_mediaroom_path_options(self, paths: List[str]) -> Any:
        self.ensure_login()
        last_error: Optional[CrestronApiError] = None
        for path in paths:
            try:
                # Some Crestron servers reject body-less POST with HTTP 411.
                return self._request("POST", path, json_body={}, include_authkey=True)
            except CrestronApiError as exc:
                last_error = exc
                continue
        if last_error:
            raise last_error
        raise CrestronApiError("media rooms operation failed", details="unknown media room action failure")

    def set_speaker_power(self, speaker_id: int, power_state: str) -> Any:
        normalized = str(power_state).strip().lower()
        if normalized not in {"on", "off"}:
            raise CrestronApiError("media rooms operation failed", details="power state must be on or off")
        sid = int(speaker_id)
        return self._post_mediaroom_path_options([
            f"/mediarooms/{sid}/power/{normalized}",
            f"/mediaRooms/{sid}/power/{normalized}",
        ])

    def set_speaker_volume(self, speaker_id: int, level_percent: int) -> Any:
        bounded = max(0, min(100, int(round(float(level_percent)))))
        sid = int(speaker_id)
        return self._post_mediaroom_path_options([
            f"/mediarooms/{sid}/volume/{bounded}",
            f"/mediaRooms/{sid}/volume/{bounded}",
        ])

    def mute_speaker(self, speaker_id: int) -> Any:
        sid = int(speaker_id)
        return self._post_mediaroom_path_options([
            f"/mediarooms/{sid}/mute",
            f"/mediaRooms/{sid}/mute",
        ])

    def unmute_speaker(self, speaker_id: int) -> Any:
        sid = int(speaker_id)
        return self._post_mediaroom_path_options([
            f"/mediarooms/{sid}/unmute",
            f"/mediaRooms/{sid}/unmute",
        ])

    def select_speaker_source(self, speaker_id: int, source_id: int) -> Any:
        sid = int(speaker_id)
        source = int(source_id)
        return self._post_mediaroom_path_options([
            f"/mediarooms/{sid}/selectsource/{source}",
            f"/mediaRooms/{sid}/selectsource/{source}",
        ])

    def set_light_state(self, light_id: int, level_raw: int) -> Any:
        self.ensure_login()
        paths = ["/lights/SetState", "/lights/setstate"]
        payloads = [
            {"lights": [{"id": int(light_id), "level": int(level_raw), "time": 0}]},
            {"lights": [{"id": int(light_id), "level": int(level_raw)}]},
            {"id": int(light_id), "value": int(level_raw)},
            {"Id": int(light_id), "Value": int(level_raw)},
            {"id": int(light_id), "level": int(level_raw)},
            {"Id": int(light_id), "Level": int(level_raw)},
        ]

        last_error: Optional[CrestronApiError] = None
        for path in paths:
            for payload in payloads:
                try:
                    return self._request(
                        "POST",
                        path,
                        json_body=payload,
                        include_authkey=True,
                    )
                except CrestronApiError as exc:
                    last_error = exc
                    continue

        if last_error:
            raise last_error
        raise CrestronApiError("lights operation failed", details="unknown SetState failure")
