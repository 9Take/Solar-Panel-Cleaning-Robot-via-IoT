"""Weather at the solar panels from Open-Meteo (free, no API key).

Standard library only. One request returns the current values and the hourly
series for the last `past_days` days, so the dashboard can draw weather next to
the battery / power history and show how solar charging follows the weather:

    temperature_2m        °C   hotter panels convert slightly less light to power
    cloud_cover           %    more cloud, less sunlight reaching the panels
    relative_humidity_2m  %    humid air / haze scatters some of the light

Used by the dashboard only; the gateway and the PLC never depend on it.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
VARIABLES = ("temperature_2m", "cloud_cover", "relative_humidity_2m")

# transport(url) -> response body; raises WeatherError when the service cannot be reached
Transport = Callable[[str], bytes]


class WeatherError(Exception):
    """Open-Meteo unreachable or returned an error."""


@dataclass(frozen=True)
class Weather:
    at: datetime                        # time of the current values (site time zone)
    current: dict[str, float | None]    # {variable: value}
    hourly: list[dict]                  # [{"time": datetime, variable: value, ...}], oldest first


def _urllib_transport(timeout_s: float) -> Transport:
    def send(url: str) -> bytes:
        request = urllib.request.Request(url, headers={"User-Agent": "solar-robot-dashboard"})
        try:
            with urllib.request.urlopen(request, timeout=timeout_s) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            return exc.read()           # Open-Meteo explains the error in a JSON body
        except (urllib.error.URLError, OSError) as exc:
            raise WeatherError(f"cannot reach Open-Meteo: {getattr(exc, 'reason', exc)}") from None
    return send


def request_url(lat: float, lon: float, tz: str, past_days: int) -> str:
    params = {
        "latitude": lat, "longitude": lon, "timezone": tz,
        "current": ",".join(VARIABLES), "hourly": ",".join(VARIABLES),
        "past_days": past_days, "forecast_days": 1,
    }
    return f"{FORECAST_URL}?{urllib.parse.urlencode(params)}"


def parse(body: bytes, tz: ZoneInfo) -> Weather:
    """Turn an Open-Meteo forecast reply into a Weather. Times are local to `tz`."""
    try:
        reply = json.loads(body)
    except ValueError:
        raise WeatherError("Open-Meteo answered with a non-JSON body") from None
    if not isinstance(reply, dict) or reply.get("error"):
        reason = reply.get("reason", "no reason given") if isinstance(reply, dict) else "unexpected reply"
        raise WeatherError(f"Open-Meteo error: {reason}")
    try:
        current, hourly = reply["current"], reply["hourly"]
        at = _local(current["time"], tz)
        now = {v: current.get(v) for v in VARIABLES}
        rows = [{"time": _local(t, tz), **{v: hourly[v][i] for v in VARIABLES}}
                for i, t in enumerate(hourly["time"])]
    except (KeyError, IndexError, TypeError, ValueError):
        raise WeatherError("unexpected reply format from Open-Meteo") from None
    return Weather(at, now, rows)


def fetch(lat: float, lon: float, tz: str, past_days: int = 7, timeout_s: float = 10.0,
          transport: Transport | None = None) -> Weather:
    send = transport or _urllib_transport(timeout_s)
    return parse(send(request_url(lat, lon, tz, past_days)), ZoneInfo(tz))


def _local(text: str, tz: ZoneInfo) -> datetime:
    """Open-Meteo sends local times without an offset when `timezone` is given."""
    return datetime.fromisoformat(text).replace(tzinfo=tz)
