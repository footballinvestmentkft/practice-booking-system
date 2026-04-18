#!/usr/bin/env python3
"""
Auth Flow Diagnostic — cookie propagation ground truth without Locust.

Traces every step of the login → CSRF init → protected POST flow using
plain requests.Session.  Determines definitively whether allow_redirects
affects access_token persistence in session.cookies.

Usage (server must be running):
  python scripts/diagnose_auth_flow.py [--host http://127.0.0.1:8001]
  python scripts/diagnose_auth_flow.py --host http://127.0.0.1:8001 \\
      --email load-user-0001@lfa.com --password LoadTest1234! --semester-id 1

Exit codes:
  0  Both redirect modes work (access_token reliably in session)
  1  One mode fails (bug confirmed — print shows root cause)
  2  Both modes fail (fundamental auth issue)
"""

import argparse
import sys
import textwrap
import requests
from http.cookiejar import CookieJar


# ── Defaults (override via CLI) ───────────────────────────────────────────────

HOST          = "http://127.0.0.1:8001"
EMAIL         = "load-user-0001@lfa.com"
PASSWORD      = "LoadTest1234!"
SEMESTER_ID   = 1
PUBLIC_EVENT  = 1  # for Init CSRF GET


# ── Helpers ───────────────────────────────────────────────────────────────────

def _short(value: str, n: int = 30) -> str:
    return value[:n] + "..." if len(value) > n else value


def _cookie_attr(cookie) -> str:
    parts = [f"domain={cookie.domain!r}", f"path={cookie.path!r}",
             f"secure={cookie.secure}"]
    if cookie.has_nonstandard_attr("HttpOnly"):
        parts.append("HttpOnly")
    extra = cookie._rest or {}
    if "SameSite" in extra:
        parts.append(f"SameSite={extra['SameSite']}")
    return "  ".join(parts)


def dump_state(label: str, session: requests.Session,
               response: requests.Response) -> None:
    """Print a structured trace for one HTTP exchange."""
    sep = "─" * 64
    print(f"\n{sep}")
    print(f"  {label}")
    print(f"  {response.request.method} {response.request.url}")
    print(sep)

    # ── Request Cookie header (what was actually sent) ────────────────────
    sent_cookie = response.request.headers.get("Cookie", "(none)")
    print(f"  REQUEST  Cookie: {sent_cookie}")

    # ── Response status + Set-Cookie raw header ───────────────────────────
    raw_sc = response.raw.headers.getlist("Set-Cookie") if hasattr(response.raw, "headers") else []
    if not raw_sc:
        raw_sc = [v for k, v in response.raw.headers.items()
                  if k.lower() == "set-cookie"] if hasattr(response.raw, "headers") else []
    print(f"  RESPONSE status: {response.status_code}")
    for sc in raw_sc:
        print(f"  RESPONSE Set-Cookie: {sc}")
    if not raw_sc:
        print(f"  RESPONSE Set-Cookie: (none)")
    if response.status_code in (301, 302, 303, 307, 308):
        print(f"  RESPONSE Location: {response.headers.get('location', '')}")

    # ── Per-response cookies (response.cookies) ───────────────────────────
    resp_names = [c.name for c in response.cookies]
    print(f"  response.cookies keys: {resp_names or '(empty)'}")

    # ── Session cookie jar state ──────────────────────────────────────────
    print(f"  session.cookies:")
    found = list(session.cookies)
    if not found:
        print(f"    (empty)")
    for c in found:
        print(f"    {c.name}={_short(c.value)}  [{_cookie_attr(c)}]")


def run_scenario(host: str, allow_redirects: bool) -> bool:
    """
    Full login → Init CSRF → browse → enroll flow.
    Returns True if enroll POST returns 303 (business success or known error).
    """
    bar = "═" * 70
    print(f"\n{bar}")
    print(f"  SCENARIO  allow_redirects={allow_redirects}")
    print(f"{bar}")

    session = requests.Session()

    # ── Step 1: POST /login ───────────────────────────────────────────────
    resp_login = session.post(
        f"{host}/login",
        data={"email": EMAIL, "password": PASSWORD},
        allow_redirects=allow_redirects,
    )
    dump_state("1. POST /login", session, resp_login)

    has_access_after_login  = bool(session.cookies.get("access_token"))
    has_csrf_after_login    = bool(session.cookies.get("csrf_token"))
    print(f"\n  ► access_token in session: {'✅ YES' if has_access_after_login else '❌ NO'}")
    print(f"  ► csrf_token   in session: {'✅ YES' if has_csrf_after_login else '❌ NO'}")

    if not has_access_after_login:
        print(f"\n  ⛔ LOGIN DID NOT PERSIST access_token — aborting scenario")
        return False

    # ── Step 2: GET /events/{id} — Init CSRF (public) ────────────────────
    resp_csrf = session.get(f"{host}/events/{PUBLIC_EVENT}")
    dump_state("2. GET /events/{id} (Init CSRF)", session, resp_csrf)

    csrf_token = session.cookies.get("csrf_token", "")
    print(f"\n  ► csrf_token after Init CSRF: {'✅ ' + _short(csrf_token) if csrf_token else '❌ (empty)'}")

    # ── Step 3: GET /semesters/enroll (authenticated browse) ─────────────
    resp_browse = session.get(f"{host}/semesters/enroll", allow_redirects=False)
    dump_state("3. GET /semesters/enroll", session, resp_browse)
    print(f"\n  ► browse status: {resp_browse.status_code} "
          f"{'✅ (authenticated)' if resp_browse.status_code == 200 else '⚠️ (redirected/error)'}")

    # ── Step 4: POST /semesters/request-enrollment ────────────────────────
    headers = {"X-CSRF-Token": csrf_token} if csrf_token else {}
    resp_enroll = session.post(
        f"{host}/semesters/request-enrollment",
        data={"semester_id": str(SEMESTER_ID)},
        headers=headers,
        allow_redirects=False,
    )
    dump_state("4. POST /semesters/request-enrollment", session, resp_enroll)

    loc = resp_enroll.headers.get("location", "")
    status_ok   = resp_enroll.status_code == 303
    csrf_ok     = resp_enroll.status_code != 403
    auth_ok     = resp_enroll.status_code != 401
    business_ok = "error" not in loc  # 303 without error = real enroll

    print(f"\n  ► enroll status  : {resp_enroll.status_code}")
    print(f"  ► location       : {loc or '(none)'}")
    print(f"  ► CSRF passed    : {'✅' if csrf_ok else '❌ 403 CSRF rejected'}")
    print(f"  ► Auth passed    : {'✅' if auth_ok else '❌ 401 no auth'}")
    print(f"  ► Business logic : {'✅ enrolled / known error' if status_ok else '❌'}")

    return status_ok


def main() -> int:
    global EMAIL, PASSWORD, SEMESTER_ID, PUBLIC_EVENT

    parser = argparse.ArgumentParser(description="Auth flow cookie diagnostic")
    parser.add_argument("--host",        default=HOST)
    parser.add_argument("--email",       default=EMAIL)
    parser.add_argument("--password",    default=PASSWORD)
    parser.add_argument("--semester-id", type=int, default=SEMESTER_ID)
    parser.add_argument("--event-id",    type=int, default=PUBLIC_EVENT)
    args = parser.parse_args()

    host        = args.host
    EMAIL       = args.email
    PASSWORD    = args.password
    SEMESTER_ID = args.semester_id
    PUBLIC_EVENT = args.event_id

    print(f"Target: {host}  |  user: {EMAIL}  |  semester: {SEMESTER_ID}")

    ok_false = run_scenario(host, allow_redirects=False)
    ok_true  = run_scenario(host, allow_redirects=True)

    print(f"\n{'═'*70}")
    print(f"  RESULTS")
    print(f"{'═'*70}")
    print(f"  allow_redirects=False : {'✅ PASS' if ok_false else '❌ FAIL'}")
    print(f"  allow_redirects=True  : {'✅ PASS' if ok_true  else '❌ FAIL'}")

    if ok_false and ok_true:
        print("\n  ✅ Both modes work — allow_redirects is irrelevant.")
        print("     Root cause lies elsewhere (e.g. CSRF timing, token rotation).")
        return 0
    elif ok_false and not ok_true:
        print(textwrap.dedent("""
          ⚠️  allow_redirects=True loses the session state needed for enroll.
             Check the trace above for the first step where session.cookies
             diverges between the two modes.
        """))
        return 1
    elif not ok_false and ok_true:
        print("\n  ⚠️  allow_redirects=False fails — unexpected. Check trace.")
        return 1
    else:
        print("\n  ⛔ CRITICAL: both modes fail — check server connectivity / seed.")
        return 2


if __name__ == "__main__":
    sys.exit(main())
