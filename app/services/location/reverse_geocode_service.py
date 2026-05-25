"""Reverse geocoding service — lat/lon → human-readable location label.

Uses Nominatim (OpenStreetMap). No API key required.
Reusable: designed for Location Platform Phase 2+.

Never raises — callers always receive a string.
Fallback: "Your location" on any timeout or HTTP error.
"""
import httpx

NOMINATIM_URL     = "https://nominatim.openstreetmap.org/reverse"
NOMINATIM_TIMEOUT = 5.0
NOMINATIM_UA      = "LFA-Practice-Booking/1.0 (footballinvestmentkft@gmail.com)"


def reverse_geocode(lat: float, lon: float) -> str:
    """Return "City, CC" label for rounded coordinates.

    lat/lon should already be 1-decimal rounded before calling.
    Returns "Your location" on any failure — never raises.
    """
    try:
        resp = httpx.get(
            NOMINATIM_URL,
            params={"lat": lat, "lon": lon, "format": "json", "zoom": 10},
            headers={"User-Agent": NOMINATIM_UA},
            timeout=NOMINATIM_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        address = data.get("address", {})
        city = (
            address.get("city")
            or address.get("town")
            or address.get("village")
            or address.get("municipality")
            or address.get("county")
        )
        cc = address.get("country_code", "").upper()
        if city and cc:
            return f"{city}, {cc}"
        if city:
            return city
        if cc:
            return f"{cc} area"
        return "Your location"
    except Exception:
        return "Your location"
