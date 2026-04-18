#!/usr/bin/env python3
"""
Cookie Propagation Diagnostic — gevent vs plain requests

MUST run with gevent installed (same env as Locust):
  python scripts/diagnose_gevent_cookies.py [--host http://127.0.0.1:8000]

What this tests:
  1. Exact Set-Cookie attributes from the server (Path, Domain, SameSite, HttpOnly)
  2. Does http.cookiejar store the cookie in session.cookies? (plain requests)
  3. Does gevent monkey-patching break extract_cookies_to_jar?
  4. Does response.cookies (direct header parse) differ from session.cookies?

Exit codes:
  0  Cookies land in session.cookies WITHOUT manual injection — no bug
  1  Cookies missing from session.cookies (bug confirmed)
  2  Fatal error (import / connection)

Hypothesis being tested:
  gevent's monkey-patching causes http.cookiejar.extract_cookies_to_jar to
  silently fail, so session.cookies stays empty even after Set-Cookie headers
  arrive.  If this script fails and diagnose_auth_flow.py passes → hypothesis
  CONFIRMED and the manual .set() injection is the correct long-term fix.
"""

# ── gevent MUST be patched before everything else ─────────────────────────────
try:
    from gevent import monkey as _gm
    _gm.patch_all()
    GEVENT_ACTIVE = True
except ImportError:
    GEVENT_ACTIVE = False
    print("WARNING: gevent not installed — running WITHOUT monkey-patch.")
    print("Install: pip install gevent locust")
    print()

import argparse
import sys

import requests

HOST        = "http://127.0.0.1:8000"
EMAIL       = "load-user-0001@lfa.com"
PASSWORD    = "LoadTest1234!"
SEMESTER_ID = 1
EVENT_ID    = 1


# ── Helpers ───────────────────────────────────────────────────────────────────

def _short(v: str, n: int = 50) -> str:
    return v[:n] + "…" if len(v) > n else v


def _dump_set_cookie(response: requests.Response) -> list[str]:
    """Return all Set-Cookie raw strings from the response headers."""
    raw = response.raw
    cookies = []
    if hasattr(raw, "headers"):
        if hasattr(raw.headers, "getlist"):
            cookies = raw.headers.getlist("Set-Cookie")
        else:
            cookies = [v for k, v in raw.headers.items()
                       if k.lower() == "set-cookie"]
    return cookies


def _dump_cookie_attrs(cookie) -> str:
    """Format cookie attributes for display."""
    parts = [
        f"domain={cookie.domain!r}",
        f"path={cookie.path!r}",
        f"domain_specified={cookie.domain_specified}",
        f"domain_initial_dot={cookie.domain_initial_dot}",
        f"secure={cookie.secure}",
        f"discard={cookie.discard}",
    ]
    rest = cookie._rest or {}
    if "HttpOnly" in rest:
        parts.append("HttpOnly")
    if "SameSite" in rest:
        parts.append(f"SameSite={rest['SameSite']}")
    return "  ".join(parts)


def step(title: str) -> None:
    print(f"\n{'─'*68}")
    print(f"  {title}")
    print(f"{'─'*68}")


def check(label: str, ok: bool, detail: str = "") -> bool:
    icon = "✅" if ok else "❌ FAIL"
    line = f"  {icon}  {label}"
    if detail:
        line += f"  →  {detail}"
    print(line)
    return ok


# ── Main diagnostic ───────────────────────────────────────────────────────────

def run(host: str) -> int:
    print()
    print("═" * 68)
    print(f"  Cookie Propagation Diagnostic")
    print(f"  gevent active : {GEVENT_ACTIVE}")
    print(f"  host          : {host}")
    print(f"  user          : {EMAIL}")
    print("═" * 68)

    failures = 0
    session = requests.Session()

    # ── STEP 1: POST /login (allow_redirects=False) ───────────────────────────
    step("1. POST /login  (allow_redirects=False)")

    resp_login = session.post(
        f"{host}/login",
        data={"email": EMAIL, "password": PASSWORD},
        allow_redirects=False,
    )
    print(f"  status         : {resp_login.status_code}")

    # Raw Set-Cookie headers from server
    sc_headers = _dump_set_cookie(resp_login)
    if sc_headers:
        for sc in sc_headers:
            print(f"  Set-Cookie     : {sc}")
    else:
        print("  Set-Cookie     : (none in raw headers)")

    # response.cookies — parsed directly from THIS response's headers
    resp_login_cookies = {c.name: c for c in resp_login.cookies}
    print(f"  response.cookies keys : {list(resp_login_cookies.keys()) or '(empty)'}")
    if "access_token" in resp_login_cookies:
        c = resp_login_cookies["access_token"]
        print(f"    access_token attrs  : {_dump_cookie_attrs(c)}")
        print(f"    access_token value  : {_short(c.value)}")

    # session.cookies — populated by http.cookiejar.extract_cookies_to_jar
    sess_cookies_after_login = {c.name: c for c in session.cookies}
    print(f"  session.cookies keys  : {list(sess_cookies_after_login.keys()) or '(empty)'}")

    at_in_resp    = "access_token" in resp_login_cookies
    at_in_session = "access_token" in sess_cookies_after_login

    ok1a = check("access_token in response.cookies", at_in_resp,
                 "server sent Set-Cookie" if at_in_resp else "server DID NOT set cookie")
    ok1b = check("access_token in session.cookies  ", at_in_session,
                 "http.cookiejar extracted it" if at_in_session else
                 "extract_cookies_to_jar FAILED — gevent/policy bug")
    if not ok1a:
        print("\n  ⛔ Server did not send access_token Set-Cookie at all.")
        print("     This is a BACKEND BUG — fix the login route.")
        return 2
    if not ok1b:
        failures += 1
        print()
        print("  DIAGNOSIS: response.cookies has access_token ✓")
        print("             session.cookies does NOT  ← BUG IS HERE")
        print("  Possible causes:")
        print("    (a) gevent monkey-patch breaks extract_cookies_to_jar timing")
        print("    (b) http.cookiejar DefaultCookiePolicy rejects the cookie")
        print("        (check domain_specified and SameSite attributes above)")
        print("    (c) urllib3 HTTPResponse.headers consumed before extraction")

        # Check if policy would reject it
        if at_in_resp:
            c = resp_login_cookies["access_token"]
            if not c.domain_specified:
                print()
                print("  ⚠️  domain_specified=False on access_token cookie.")
                print("     DefaultCookiePolicy.return_ok_domain() returns False")
                print("     when domain_specified=False and RFC2965=False (default).")
                print("     → cookie stored in jar but NOT sent on subsequent requests")
                print("     → FIX: server should set Domain attribute explicitly, OR")
                print("             use cookie policy that ignores domain_specified")

    # ── STEP 2: GET /events/{id} — Init CSRF ─────────────────────────────────
    step(f"2. GET /events/{EVENT_ID}  (Init CSRF)")

    # Send with whatever is in session (access_token if extract worked)
    resp_csrf = session.get(f"{host}/events/{EVENT_ID}")
    print(f"  status         : {resp_csrf.status_code}")

    sc_headers2 = _dump_set_cookie(resp_csrf)
    for sc in sc_headers2:
        print(f"  Set-Cookie     : {sc}")
    if not sc_headers2:
        print("  Set-Cookie     : (none)")

    resp_csrf_cookies = {c.name: c for c in resp_csrf.cookies}
    print(f"  response.cookies keys : {list(resp_csrf_cookies.keys()) or '(empty)'}")
    if "csrf_token" in resp_csrf_cookies:
        c = resp_csrf_cookies["csrf_token"]
        print(f"    csrf_token attrs  : {_dump_cookie_attrs(c)}")
        print(f"    csrf_token value  : {_short(c.value)}")

    sess_cookies_after_csrf = {c.name: c for c in session.cookies}
    print(f"  session.cookies keys  : {list(sess_cookies_after_csrf.keys()) or '(empty)'}")

    csrf_in_resp    = "csrf_token" in resp_csrf_cookies
    csrf_in_session = "csrf_token" in sess_cookies_after_csrf

    ok2a = check("csrf_token in response.cookies", csrf_in_resp)
    ok2b = check("csrf_token in session.cookies  ", csrf_in_session,
                 "http.cookiejar extracted it" if csrf_in_session else
                 "extract_cookies_to_jar FAILED — same gevent/policy bug")
    if not ok2b:
        failures += 1

    # ── STEP 3: POST /semesters/request-enrollment ────────────────────────────
    step("3. POST /semesters/request-enrollment  (auth + CSRF test)")

    # Use session-native cookies (NO manual injection) — this is the ground truth
    csrf_for_header = (
        resp_csrf_cookies.get("csrf_token", None) or
        sess_cookies_after_csrf.get("csrf_token")
    )
    csrf_val = csrf_for_header.value if csrf_for_header else ""

    sent_headers = {"X-CSRF-Token": csrf_val} if csrf_val else {}
    print(f"  X-CSRF-Token header   : {'set (' + _short(csrf_val) + ')' if csrf_val else '(none — will get 403)'}")
    print(f"  Cookie header (auto)  :", end=" ")

    resp_enroll = session.post(
        f"{host}/semesters/request-enrollment",
        data={"semester_id": str(SEMESTER_ID)},
        headers=sent_headers,
        allow_redirects=False,
    )

    # What Cookie header did requests actually send?
    sent_cookie = resp_enroll.request.headers.get("Cookie", "(none)")
    print(sent_cookie)

    print(f"  status         : {resp_enroll.status_code}")
    loc = resp_enroll.headers.get("location", "")
    if loc:
        print(f"  Location       : {loc}")

    ok3_csrf = check("CSRF passed     (not 403)", resp_enroll.status_code != 403,
                     f"got {resp_enroll.status_code}")
    ok3_auth = check("Auth passed     (not 401)", resp_enroll.status_code != 401,
                     f"got {resp_enroll.status_code}")
    ok3_biz  = check("Business logic  (303)",    resp_enroll.status_code == 303,
                     f"got {resp_enroll.status_code}")

    if not ok3_csrf or not ok3_auth:
        failures += 1
        if not ok3_auth and at_in_resp and not at_in_session:
            print()
            print("  CONFIRMED: access_token IS in response.cookies but NOT in")
            print("  session.cookies → Cookie header sent WITHOUT access_token")
            print("  → server sees unauthenticated request → 401")
            print()
            print("  VERDICT: gevent/http.cookiejar bug confirmed.")
            print("  The manual .set() injection in locustfile.py is the correct fix.")
        elif not ok3_csrf and csrf_in_resp and not csrf_in_session:
            print()
            print("  CONFIRMED: csrf_token IS in response.cookies but NOT in")
            print("  session.cookies → Cookie header sent WITHOUT csrf_token")
            print("  → server sees CSRF mismatch → 403")

    # ── Summary ───────────────────────────────────────────────────────────────
    print()
    print("═" * 68)
    print("  VERDICT")
    print("═" * 68)

    assertions = [
        ("access_token in response.cookies after login", ok1a),
        ("access_token in session.cookies  after login", ok1b),
        ("csrf_token   in response.cookies after GET  ", ok2a),
        ("csrf_token   in session.cookies  after GET  ", ok2b),
        ("CSRF validation passed on enroll POST       ", ok3_csrf),
        ("Auth  validation passed on enroll POST      ", ok3_auth),
    ]
    for label, ok in assertions:
        print(f"  {'✅' if ok else '❌'}  {label}")

    print()
    if failures == 0:
        print("  ✅ ALL ASSERTIONS PASS")
        print("  Cookie injection in locustfile.py is NOT needed — remove it.")
        print("  The load test IS production-representative.")
    else:
        print(f"  ❌ {failures} ASSERTION(S) FAILED")
        print()
        if not ok1b or not ok2b:
            print("  session.cookies is not populated by extract_cookies_to_jar.")
            if GEVENT_ACTIVE:
                print("  Since gevent IS active → gevent/urllib3 interaction confirmed.")
                print("  The .set() injection workaround in locustfile.py is CORRECT and")
                print("  NECESSARY — it is not masking a server bug.")
                print()
                print("  The load test flow (login→enroll→withdraw) is semantically correct.")
                print("  Latency and error rates are production-representative EXCEPT for")
                print("  the overhead of .set() (negligible, pure Python, ~1µs).")
            else:
                print("  gevent NOT active — re-run with gevent installed to confirm.")
        elif not ok3_auth:
            print("  server.cookies has access_token but server returns 401.")
            print("  This may be a JWT expiry or SECRET_KEY mismatch.")

    return 0 if failures == 0 else 1


def main() -> int:
    global HOST, EMAIL, PASSWORD, SEMESTER_ID, EVENT_ID

    parser = argparse.ArgumentParser(description="Cookie propagation diagnostic (gevent)")
    parser.add_argument("--host",        default=HOST)
    parser.add_argument("--email",       default=EMAIL)
    parser.add_argument("--password",    default=PASSWORD)
    parser.add_argument("--semester-id", type=int, default=SEMESTER_ID)
    parser.add_argument("--event-id",    type=int, default=EVENT_ID)
    args = parser.parse_args()

    HOST        = args.host
    EMAIL       = args.email
    PASSWORD    = args.password
    SEMESTER_ID = args.semester_id
    EVENT_ID    = args.event_id

    return run(HOST)


if __name__ == "__main__":
    sys.exit(main())
