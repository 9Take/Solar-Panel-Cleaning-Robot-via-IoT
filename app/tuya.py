"""Minimal Tuya Cloud (OpenAPI) client: read a device's data points (DPs).

Standard library only. Requests are signed with HMAC-SHA256 as described in
Tuya's "Sign requests for cloud authorization":

    stringToSign = METHOD \n sha256(body) \n (signed headers, none here) \n path?sorted_query
    token request:     sign = HMAC(secret, client_id + t + nonce + stringToSign)
    business request:  sign = HMAC(secret, client_id + access_token + t + nonce + stringToSign)

The access token is cached and refreshed shortly before it expires, or once when
Tuya reports it invalid. The access secret and token never appear in errors or logs.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable

TOKEN_PATH = "/v1.0/token?grant_type=1"
STATUS_PATH = "/v1.0/iot-03/devices/{device_id}/status"
TOKEN_REFRESH_MARGIN_S = 60
TOKEN_INVALID_CODES = {1010, 1011}   # token invalid / token expired

# transport(method, url, headers, body) -> (http_status, response_body)
Transport = Callable[[str, str, dict[str, str], bytes], tuple[int, bytes]]


class TuyaError(Exception):
    """Tuya Cloud unreachable or returned an error."""


def _urllib_transport(timeout_s: float) -> Transport:
    def send(method: str, url: str, headers: dict[str, str], body: bytes) -> tuple[int, bytes]:
        request = urllib.request.Request(url, data=body or None, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=timeout_s) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()
        except (urllib.error.URLError, OSError) as exc:
            reason = getattr(exc, "reason", exc)
            raise TuyaError(f"cannot reach Tuya Cloud: {reason}") from None
    return send


def string_to_sign(method: str, path: str, body: bytes = b"") -> str:
    """Canonical request string. Query parameters are sorted by name."""
    base, _, query = path.partition("?")
    if query:
        params = sorted(pair.split("=", 1) for pair in query.split("&"))
        base += "?" + "&".join(f"{k}={v}" for k, v in params)
    return "\n".join([method.upper(), hashlib.sha256(body).hexdigest(), "", base])


def sign(secret: str, client_id: str, t_ms: str, nonce: str, to_sign: str, access_token: str = "") -> str:
    message = client_id + access_token + t_ms + nonce + to_sign
    return hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest().upper()


class TuyaCloud:
    def __init__(
        self,
        endpoint: str,
        access_id: str,
        access_secret: str,
        timeout_s: float = 10.0,
        transport: Transport | None = None,
        clock: Callable[[], float] = time.time,
        nonce: Callable[[], str] = lambda: uuid.uuid4().hex,
    ) -> None:
        self.endpoint = endpoint.rstrip("/")
        self._id, self._secret = access_id, access_secret
        self._send = transport or _urllib_transport(timeout_s)
        self._clock, self._nonce = clock, nonce
        self._token: str | None = None
        self._token_expires = 0.0

    def device_status(self, device_id: str) -> dict[str, object]:
        """Current DP values of a device as {dp_code: value}."""
        result = self._request("GET", STATUS_PATH.format(device_id=device_id))
        if not isinstance(result, list):
            raise TuyaError("unexpected device status format from Tuya Cloud")
        return {item["code"]: item["value"] for item in result if "code" in item}

    def _request(self, method: str, path: str) -> object:
        try:
            return self._call(method, path, self._access_token())
        except _TokenInvalid:
            self._token = None                    # refresh once, no further retries
        try:
            return self._call(method, path, self._access_token())
        except _TokenInvalid:
            raise TuyaError("Tuya Cloud rejected a freshly issued token") from None

    def _access_token(self) -> str:
        if self._token is None or self._clock() >= self._token_expires - TOKEN_REFRESH_MARGIN_S:
            result = self._call("GET", TOKEN_PATH, token="")
            try:
                self._token = result["access_token"]
                self._token_expires = self._clock() + float(result["expire_time"])
            except (TypeError, KeyError, ValueError):
                raise TuyaError("unexpected token response from Tuya Cloud") from None
        return self._token

    def _call(self, method: str, path: str, token: str) -> object:
        t_ms, nonce = str(int(self._clock() * 1000)), self._nonce()
        headers = {
            "client_id": self._id,
            "t": t_ms,
            "nonce": nonce,
            "sign_method": "HMAC-SHA256",
            "sign": sign(self._secret, self._id, t_ms, nonce, string_to_sign(method, path), token),
        }
        if token:
            headers["access_token"] = token
        status, body = self._send(method, self.endpoint + path, headers, b"")
        try:
            reply = json.loads(body)
        except ValueError:
            raise TuyaError(f"Tuya Cloud answered HTTP {status} with a non-JSON body") from None
        if not reply.get("success"):
            code, msg = reply.get("code"), reply.get("msg", "no message")
            if token and code in TOKEN_INVALID_CODES:
                raise _TokenInvalid()
            raise TuyaError(f"Tuya Cloud error {code}: {msg}")
        return reply.get("result")


class _TokenInvalid(Exception):
    pass


def main() -> None:
    """`python -m app.tuya`: print every DP of TUYA_DEVICE_ID (to find TUYA_BATTERY_DP)."""
    from app.config import Settings

    settings = Settings()
    missing = [k for k in settings.missing_tuya_keys() if k != "TUYA_BATTERY_DP"]
    if missing:
        raise SystemExit(f"Fill in {', '.join(missing)} in .env first")
    cloud = TuyaCloud(settings.tuya_api_endpoint, settings.tuya_access_id,
                      settings.tuya_access_secret.get_secret_value(), settings.tuya_timeout_s)
    try:
        status = cloud.device_status(settings.tuya_device_id)
    except TuyaError as exc:
        raise SystemExit(str(exc)) from None
    width = max((len(code) for code in status), default=0)
    for code, value in status.items():
        marker = "  <- TUYA_BATTERY_DP" if code == settings.tuya_battery_dp else ""
        print(f"{code:<{width}}  {value!r}{marker}")


if __name__ == "__main__":
    main()
