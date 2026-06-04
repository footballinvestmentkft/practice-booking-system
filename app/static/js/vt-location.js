/**
 * vt-location.js — Phase 1 browser timezone + geolocation helper.
 *
 * Exposes window.VtLocation with three methods:
 *   getBrowserTimezone()   → IANA tz string, "UTC" on error
 *   warmUp()               → starts async GPS fix; populates internal cache
 *   buildSubmitPayload()   → returns {browser_timezone, location:{...}} ready
 *                            to merge into any VT game submit payload
 *
 * Cache: sessionStorage key "vtloc_cache", TTL 5 minutes.
 * Staleness: GPS fix older than 5 minutes is classified "stale_browser_geolocation".
 * No-permission / no-GPS: location.source = "unavailable", lat/lng/etc. null.
 *
 * Phase 2 will add lat/lng → IANA timezone derivation (timezonefinder) here
 * without changing the buildSubmitPayload() contract.
 */
(function (root) {
    'use strict';

    var _CACHE_KEY      = 'vtloc_cache';
    var _CACHE_TTL_MS   = 5 * 60 * 1000;   // 5 minutes
    var _STALE_LIMIT_MS = 5 * 60 * 1000;   // re-classified stale after 5 min

    // Internal location state — set by warmUp(), read by buildSubmitPayload()
    var _loc = null;  // null → unavailable; object → {lat, lng, accuracy_m, captured_at}

    // ── Public: timezone ──────────────────────────────────────────────────────

    function getBrowserTimezone() {
        try {
            return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC';
        } catch (e) {
            return 'UTC';
        }
    }

    // ── Cache helpers ─────────────────────────────────────────────────────────

    function _loadCache() {
        try {
            var raw = sessionStorage.getItem(_CACHE_KEY);
            if (!raw) return null;
            var obj = JSON.parse(raw);
            if (!obj || typeof obj._ts !== 'number') return null;
            if (Date.now() - obj._ts > _CACHE_TTL_MS) {
                sessionStorage.removeItem(_CACHE_KEY);
                return null;
            }
            return obj;
        } catch (e) { return null; }
    }

    function _saveCache(lat, lng, accuracy_m, captured_at) {
        try {
            sessionStorage.setItem(_CACHE_KEY, JSON.stringify({
                lat: lat,
                lng: lng,
                accuracy_m: accuracy_m,
                captured_at: captured_at,
                _ts: Date.now(),
            }));
        } catch (e) { /* sessionStorage unavailable — ignore */ }
    }

    // ── Staleness classifier ──────────────────────────────────────────────────

    function _classifySource(captured_at) {
        if (!captured_at) return 'unavailable';
        var ageMs = Date.now() - new Date(captured_at).getTime();
        return ageMs <= _STALE_LIMIT_MS ? 'browser_geolocation' : 'stale_browser_geolocation';
    }

    // ── Public: warm-up (call once on page load) ──────────────────────────────

    function warmUp() {
        // Try sessionStorage cache first (synchronous, safe for submit)
        var cached = _loadCache();
        if (cached && cached.lat != null && cached.lng != null) {
            _loc = {
                lat:         cached.lat,
                lng:         cached.lng,
                accuracy_m:  cached.accuracy_m,
                captured_at: cached.captured_at,
            };
            return;
        }

        if (!navigator || !navigator.geolocation) return;

        navigator.geolocation.getCurrentPosition(
            function (pos) {
                var now = new Date().toISOString();
                _loc = {
                    lat:         pos.coords.latitude,
                    lng:         pos.coords.longitude,
                    accuracy_m:  Math.round(pos.coords.accuracy),
                    captured_at: now,
                };
                _saveCache(_loc.lat, _loc.lng, _loc.accuracy_m, _loc.captured_at);
            },
            function () { /* denied or unavailable — _loc stays null */ },
            { enableHighAccuracy: true, timeout: 10000, maximumAge: 0 }
        );
    }

    // ── Public: build submit payload fields ───────────────────────────────────

    function buildSubmitPayload() {
        var tz  = getBrowserTimezone();
        var loc = _loc;

        if (loc && loc.lat != null && loc.lng != null) {
            return {
                browser_timezone: tz,
                location: {
                    lat:         loc.lat,
                    lng:         loc.lng,
                    accuracy_m:  loc.accuracy_m,
                    captured_at: loc.captured_at,
                    source:      _classifySource(loc.captured_at),
                },
            };
        }

        return {
            browser_timezone: tz,
            location: {
                lat:         null,
                lng:         null,
                accuracy_m:  null,
                captured_at: null,
                source:      'unavailable',
            },
        };
    }

    // ── Export ────────────────────────────────────────────────────────────────

    root.VtLocation = {
        getBrowserTimezone: getBrowserTimezone,
        warmUp:             warmUp,
        buildSubmitPayload: buildSubmitPayload,
    };

}(typeof window !== 'undefined' ? window : this));
