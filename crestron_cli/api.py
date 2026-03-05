from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

try:
    import requests
except Exception:
    requests = None

from .config import Config


ERROR_SOURCE_MAP = {
    5001: "Session expired",
    5002: "Authentication failed",
    7003: "Lights operation error",
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
        if requests is None:
            raise CrestronApiError("missing dependency", details="install requests")
        self.config = config
        self.session = requests.Session()
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
            headers["Crestron-RestAPI-AuthKey"] = self.authkey
        return headers

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: Optional[Dict[str, Any]] = None,
        include_authkey: bool,
        retry_on_auth: bool = True,
    ) -> Any:
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
            raise CrestronApiError("request failed", details=str(exc))

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
            out.append({"id": scene_id, "name": str(name or f"Scene {scene_id}"), "room_id": room_id})
        return out

    def set_light_state(self, light_id: int, level_raw: int) -> Any:
        self.ensure_login()
        paths = ["/lights/SetState", "/lights/setstate"]
        payloads = [
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
