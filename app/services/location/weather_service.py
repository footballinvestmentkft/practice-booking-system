"""Weather service — fetch current weather from Open-Meteo.

Uses Open-Meteo Forecast API. No API key required.
Backend-side only — never called during public profile render.

TTL: WEATHER_TTL_MINUTES documents the intended cache lifetime.
Refresh only happens when the user re-saves the widget in the editor.
"""
from typing import Any

import httpx

WEATHER_BASE_URL    = "https://api.open-meteo.com/v1/forecast"
WEATHER_TIMEOUT_S   = 8.0
WEATHER_TTL_MINUTES = 30  # documented cache lifetime — no background scheduler

# WMO Weather Interpretation Codes → human-readable condition string
WMO_CONDITIONS: dict[int, str] = {
    0:  "Clear sky",
    1:  "Mainly clear",
    2:  "Partly cloudy",
    3:  "Overcast",
    45: "Fog",
    48: "Icy fog",
    51: "Light drizzle",
    53: "Moderate drizzle",
    55: "Dense drizzle",
    61: "Slight rain",
    63: "Moderate rain",
    65: "Heavy rain",
    71: "Slight snow",
    73: "Moderate snow",
    75: "Heavy snow",
    80: "Slight showers",
    81: "Moderate showers",
    82: "Violent showers",
    95: "Thunderstorm",
    96: "Thunderstorm with hail",
    99: "Thunderstorm with heavy hail",
}


def fetch_current_weather(lat: float, lon: float, units: str = "metric") -> dict[str, Any]:
    """Fetch current weather for coordinates from Open-Meteo.

    lat/lon: fetch-precision values (2-decimal rounded).
    units: "metric" (°C, km/h) or "imperial" (°F, mph).
    Raises httpx.TimeoutException or httpx.HTTPError on failure —
    callers (build_weather_module) catch these and store as fetch_error.
    """
    temperature_unit = "celsius" if units == "metric" else "fahrenheit"
    windspeed_unit   = "kmh"     if units == "metric" else "mph"

    resp = httpx.get(
        WEATHER_BASE_URL,
        params={
            "latitude":         lat,
            "longitude":        lon,
            "current":          "temperature_2m,weathercode,windspeed_10m,relative_humidity_2m",
            "temperature_unit": temperature_unit,
            "windspeed_unit":   windspeed_unit,
            "timezone":         "UTC",
        },
        timeout=WEATHER_TIMEOUT_S,
    )
    resp.raise_for_status()
    current     = resp.json().get("current", {})
    weathercode = int(current.get("weathercode", 0))
    return {
        "temp_c":      round(float(current.get("temperature_2m", 0)), 1),
        "weathercode": weathercode,
        "condition":   WMO_CONDITIONS.get(weathercode, "Unknown"),
        "wind_kph":    round(float(current.get("windspeed_10m", 0)), 1),
        "humidity":    int(current.get("relative_humidity_2m", 0)),
    }
