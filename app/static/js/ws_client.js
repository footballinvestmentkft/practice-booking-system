/**
 * ws_client.js — Per-user WebSocket event stream client.
 *
 * Connects to /ws/events?token=<JWT access_token> and dispatches
 * CustomEvents on `document` so individual page scripts can listen:
 *
 *   document.addEventListener('ws:challenge_sent',       e => { ... e.detail ... })
 *   document.addEventListener('ws:challenge_accepted',   e => { ... })
 *   document.addEventListener('ws:challenge_declined',   e => { ... })
 *   document.addEventListener('ws:challenge_cancelled',  e => { ... })
 *   document.addEventListener('ws:challenge_completed',  e => { ... })
 *   document.addEventListener('ws:challenge_forfeited',  e => { ... })
 *   document.addEventListener('ws:challenge_no_contest', e => { ... })
 *   document.addEventListener('ws:challenge_live_started', e => { ... })
 *   document.addEventListener('ws:challenge_expired',    e => { ... })
 *   document.addEventListener('ws:notification_created', e => { ... })
 *
 * On connect failure or redis_unavailable, falls back to polling.
 * Reconnect strategy: exponential back-off 1s→2s→4s…→30s.
 * After 3 consecutive failures: emit ws:fallback event.
 *
 * Token is fetched from /api/ws-token (same-origin, reads HttpOnly cookie
 * server-side so the raw JWT never appears in HTML/localStorage).
 */
(function () {
  'use strict';

  // ── Config ────────────────────────────────────────────────────────────────
  const MAX_BACKOFF_MS   = 30_000;
  const MAX_FAIL_BEFORE_FALLBACK = 3;
  const PING_TIMEOUT_MS  = 90_000;   // server pings every 30s; 3× grace period

  // ── State ─────────────────────────────────────────────────────────────────
  let ws            = null;
  let backoffMs     = 1_000;
  let failCount     = 0;
  let inFallback    = false;
  let pingTimer     = null;
  let reconnectTimer = null;
  let destroyed     = false;
  let cachedToken   = null;
  let _fetchCtrl    = null;   // AbortController for the /api/ws-token fetch

  // ── Teardown on page unload (prevents ERR_ABORTED race during navigation) ─
  window.addEventListener('beforeunload', function() {
    destroyed = true;
    if (_fetchCtrl) { _fetchCtrl.abort(); _fetchCtrl = null; }
    clearPingTimer();
    if (reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = null; }
    if (ws) { try { ws.close(); } catch (_) {} ws = null; }
  });

  // ── Token fetch ───────────────────────────────────────────────────────────
  function fetchTokenAndConnect() {
    if (destroyed || inFallback) return;
    if (_fetchCtrl) { _fetchCtrl.abort(); }
    _fetchCtrl = new AbortController();
    fetch('/api/ws-token', { credentials: 'same-origin', signal: _fetchCtrl.signal })
      .then(function(r) { return r.ok ? r.json() : null; })
      .then(function(data) {
        _fetchCtrl = null;
        if (!data || !data.token) return;  // not logged in
        cachedToken = data.token;
        _openSocket(data.token);
      })
      .catch(function() { _fetchCtrl = null; });
  }

  function connect() {
    if (destroyed || inFallback) return;
    if (cachedToken) {
      _openSocket(cachedToken);
    } else {
      fetchTokenAndConnect();
    }
  }

  function _openSocket(token) {
    if (destroyed || inFallback) return;
    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
    const url   = proto + '://' + location.host + '/ws/events?token=' + encodeURIComponent(token);

    try {
      ws = new WebSocket(url);
    } catch (e) {
      scheduleReconnect();
      return;
    }

    ws.onopen = () => {
      backoffMs  = 1_000;
      failCount  = 0;
      inFallback = false;
      resetPingTimer();
      dispatch('ws:connected', {});
    };

    ws.onmessage = (ev) => {
      resetPingTimer();
      let data;
      try { data = JSON.parse(ev.data); } catch { return; }

      if (data.type === 'ping') return;

      if (data.type === 'redis_unavailable') {
        enterFallback();
        return;
      }

      dispatch(`ws:${data.type}`, data);
    };

    ws.onerror = () => {
      // onerror is always followed by onclose — handle there
    };

    ws.onclose = (ev) => {
      clearPingTimer();
      // 4001 = invalid token; no point reconnecting
      if (ev.code === 4001) {
        dispatch('ws:auth_failed', { code: ev.code });
        return;
      }
      failCount += 1;
      if (failCount >= MAX_FAIL_BEFORE_FALLBACK) {
        enterFallback();
        return;
      }
      scheduleReconnect();
    };
  }

  function scheduleReconnect() {
    if (destroyed || inFallback) return;
    reconnectTimer = setTimeout(() => {
      backoffMs = Math.min(backoffMs * 2, MAX_BACKOFF_MS);
      connect();
    }, backoffMs);
  }

  function resetPingTimer() {
    clearPingTimer();
    pingTimer = setTimeout(() => {
      // Server missed a ping — treat as disconnected
      if (ws) ws.close();
    }, PING_TIMEOUT_MS);
  }

  function clearPingTimer() {
    if (pingTimer) { clearTimeout(pingTimer); pingTimer = null; }
  }

  function enterFallback() {
    if (inFallback) return;
    inFallback = true;
    if (ws) { try { ws.close(); } catch (_) {} }
    dispatch('ws:fallback', {});
  }

  // ── Visibility reconnect ──────────────────────────────────────────────────
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible' && !inFallback && !destroyed) {
      if (!ws || ws.readyState === WebSocket.CLOSED) {
        if (reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = null; }
        backoffMs = 1_000;
        connect();
      }
    }
  });

  // ── Helpers ───────────────────────────────────────────────────────────────
  function dispatch(name, detail) {
    document.dispatchEvent(new CustomEvent(name, { detail, bubbles: false }));
  }

  // ── Public API ────────────────────────────────────────────────────────────
  window.WsClient = {
    destroy() {
      destroyed = true;
      clearPingTimer();
      if (reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = null; }
      if (ws) { try { ws.close(); } catch (_) {} ws = null; }
    },
    isConnected() { return ws && ws.readyState === WebSocket.OPEN; },
    isFallback()  { return inFallback; },
  };

  // ── Auto-start ────────────────────────────────────────────────────────────
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', fetchTokenAndConnect);
  } else {
    fetchTokenAndConnect();
  }
})();
