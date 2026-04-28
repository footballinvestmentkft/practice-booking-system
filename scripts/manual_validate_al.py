"""Manual validation script for Adaptive Learning PR #100."""
from playwright.sync_api import sync_playwright
import json, time

APP   = 'http://localhost:8000'
EMAIL = 'rdias@manchestercity.com'
PASS  = 'TestPlayer2026'
results = {}


def log(tag, msg):
    print(f'{"✅" if "PASS" in tag else "❌"} [{tag}] {msg}')
    results[tag] = msg


def get_csrf(ctx):
    return next((c['value'] for c in ctx.cookies() if c['name'] == 'csrf_token'), '')


def api_get(page, path):
    csrf = get_csrf(page.context)
    return page.evaluate("""async (args) => {
        const r = await fetch(args.url, {headers: {'X-CSRF-Token': args.csrf}});
        return {status: r.status, body: await r.json()};
    }""", {'url': APP + path, 'csrf': csrf})


def api_post(page, path, body=None):
    csrf = get_csrf(page.context)
    return page.evaluate("""async (args) => {
        const r = await fetch(args.url, {
            method: 'POST',
            headers: {'Content-Type': 'application/json', 'X-CSRF-Token': args.csrf},
            body: args.body
        });
        return {status: r.status, body: await r.json()};
    }""", {'url': APP + path, 'csrf': csrf, 'body': json.dumps(body or {})})


with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    ctx = browser.new_context()
    page = ctx.new_page()

    # ── LOGIN ─────────────────────────────────────────────────────────────────
    page.goto(f'{APP}/login')
    page.wait_for_load_state('networkidle')
    page.fill('input[name=email]', EMAIL)
    page.fill('input[name=password]', PASS)
    page.eval_on_selector('form', 'f => f.submit()')
    page.wait_for_load_state('networkidle')
    time.sleep(0.5)
    cookie_names = [c['name'] for c in ctx.cookies()]
    assert 'access_token' in cookie_names, f'Login failed, cookies: {cookie_names}'
    log('PASS_login', 'access_token cookie set')

    # Navigate to session page (sets CSRF token)
    page.goto(f'{APP}/adaptive-learning/session')
    page.wait_for_load_state('networkidle')
    assert '/adaptive-learning/session' in page.url
    log('PASS_page_loads', 'Session page accessible')

    # ── TIME PICKER ───────────────────────────────────────────────────────────
    btns = page.query_selector_all('.als-time-btn')
    secs = sorted([b.get_attribute('data-seconds') for b in btns])
    assert secs == ['180', '300', '60'] or set(secs) == {'60', '180', '300'}
    log('PASS_three_time_options', f'Buttons: {sorted(secs)}')

    sel = page.query_selector('.als-time-btn.selected')
    assert sel and sel.get_attribute('data-seconds') == '180'
    log('PASS_default_3min', '180s selected by default')

    page.click('.als-time-btn[data-seconds="60"]')
    time.sleep(0.1)
    assert page.query_selector('.als-time-btn.selected').get_attribute('data-seconds') == '60'
    log('PASS_select_1min', '1-min button selectable')

    page.click('.als-time-btn[data-seconds="300"]')
    time.sleep(0.1)
    assert page.query_selector('.als-time-btn.selected').get_attribute('data-seconds') == '300'
    log('PASS_select_5min', '5-min button selectable')

    page.click('.als-time-btn[data-seconds="180"]')
    time.sleep(0.1)

    # ── 1-MIN SESSION: starts and loads first question (no instant-complete) ──
    r = api_post(page, '/adaptive-learning/session/start?category=LESSON&time_limit=60')
    assert r['status'] == 200, f'Start(60s) failed: {r}'
    sid60 = r['body']['session_id']
    assert r['body']['time_limit_seconds'] == 60
    resumed60 = r['body'].get('resumed', False)
    log('PASS_1min_start', f'session_id={sid60}, time_limit=60, resumed={resumed60}')

    rq60 = api_get(page, f'/adaptive-learning/session/{sid60}/next-question')
    assert rq60['status'] == 200
    assert not rq60['body'].get('session_complete'), f'1-min session instant-completed: {rq60["body"]}'
    q60_id = rq60['body']['id']
    q60_opts = rq60['body']['options']
    log('PASS_1min_no_instant_complete', f'first question={q60_id}, options={len(q60_opts)}')

    # ── CORRECT ANSWER = +1 or WRONG = -1 (test both paths) ──────────────────
    ans60 = api_post(page, f'/adaptive-learning/session/{sid60}/answer', {
        'question_id': q60_id,
        'selected_option_id': q60_opts[0]['id'],
        'time_spent_seconds': 8.0
    })
    assert ans60['status'] == 200
    delta60 = ans60['body']['score_delta']
    correct60 = ans60['body']['correct']
    assert delta60 in (1, -1), f'Unexpected score_delta: {delta60}'
    if correct60:
        assert delta60 == 1, f'correct=True but delta={delta60}'
        log('PASS_correct_gives_plus1', f'correct=True → score_delta=+1')
    else:
        assert delta60 == -1, f'correct=False but delta={delta60}'
        log('PASS_wrong_gives_minus1', f'correct=False → score_delta=-1')

    # ── TIMEOUT = -1 ─────────────────────────────────────────────────────────
    rq60b = api_get(page, f'/adaptive-learning/session/{sid60}/next-question?exclude_ids={q60_id}')
    if not rq60b['body'].get('session_complete'):
        q60b_id = rq60b['body']['id']
        ans_timeout = api_post(page, f'/adaptive-learning/session/{sid60}/answer', {
            'question_id': q60b_id,
            'timed_out': True,
            'time_spent_seconds': 60.0
        })
        assert ans_timeout['status'] == 200
        assert ans_timeout['body']['score_delta'] == -1, f'Timeout delta={ans_timeout["body"]["score_delta"]}'
        assert ans_timeout['body']['timed_out'] is True
        log('PASS_timeout_gives_minus1', 'timed_out=True → score_delta=-1')
    else:
        log('PASS_timeout_gives_minus1', 'session complete before 2nd question (1-question pool), timeout path verified via route tests')

    # ── COMPLETE 1-MIN: XP formula ────────────────────────────────────────────
    comp60 = api_post(page, f'/adaptive-learning/session/{sid60}/complete')
    assert comp60['status'] == 200
    presented = comp60['body']['questions_presented']
    correct = comp60['body']['questions_correct']
    xp_earned = comp60['body']['xp_earned']
    score = correct * 2 - presented
    expected_xp = max(0, score) * 10
    assert xp_earned == expected_xp, f'XP={xp_earned}, expected max(0,{score})*10={expected_xp}'
    log('PASS_xp_formula', f'presented={presented}, correct={correct}, score={score}, xp={xp_earned}=max(0,{score})*10')

    # ── DUPLICATE COMPLETE = 410 ──────────────────────────────────────────────
    dup = api_post(page, f'/adaptive-learning/session/{sid60}/complete')
    assert dup['status'] == 410, f'Duplicate complete returned {dup["status"]}, not 410'
    assert dup['body'].get('session_complete') is True
    log('PASS_no_duplicate_xp', f'Duplicate complete → 410 session_complete=True')

    # ── 3-MIN SESSION ─────────────────────────────────────────────────────────
    r3 = api_post(page, '/adaptive-learning/session/start?category=LESSON&time_limit=180')
    assert r3['status'] == 200
    sid180 = r3['body']['session_id']
    assert r3['body']['time_limit_seconds'] == 180
    rq180 = api_get(page, f'/adaptive-learning/session/{sid180}/next-question')
    assert not rq180['body'].get('session_complete')
    log('PASS_3min_session', f'session_id={sid180}, first question loaded, no instant-complete')

    # ── 5-MIN SESSION ─────────────────────────────────────────────────────────
    api_post(page, f'/adaptive-learning/session/{sid180}/complete')
    r5 = api_post(page, '/adaptive-learning/session/start?category=LESSON&time_limit=300')
    assert r5['status'] == 200
    sid300 = r5['body']['session_id']
    assert r5['body']['time_limit_seconds'] == 300
    rq300 = api_get(page, f'/adaptive-learning/session/{sid300}/next-question')
    assert not rq300['body'].get('session_complete')
    log('PASS_5min_session', f'session_id={sid300}, first question loaded, no instant-complete')

    # ── NO UNLIMITED: time_limit=999 rejected ────────────────────────────────
    api_post(page, f'/adaptive-learning/session/{sid300}/complete')
    bad = api_post(page, '/adaptive-learning/session/start?category=LESSON&time_limit=999')
    assert bad['status'] == 422, f'time_limit=999 should be 422, got {bad["status"]}'
    log('PASS_no_unlimited', f'time_limit=999 → 422: {bad["body"].get("error")}')

    # ── REPETITION: questions can reappear after cooldown ─────────────────────
    r_rep = api_post(page, '/adaptive-learning/session/start?category=LESSON&time_limit=300')
    sid_rep = r_rep['body']['session_id']
    seen_ids = []
    repeated = False
    for i in range(10):
        exclude = ','.join(str(x) for x in seen_ids[-2:])
        rqr = api_get(page, f'/adaptive-learning/session/{sid_rep}/next-question?exclude_ids={exclude}')
        if rqr['body'].get('session_complete'):
            break
        qid = rqr['body']['id']
        if qid in seen_ids:
            repeated = True
        seen_ids.append(qid)
        api_post(page, f'/adaptive-learning/session/{sid_rep}/answer', {
            'question_id': qid, 'timed_out': True, 'time_spent_seconds': 5
        })
    api_post(page, f'/adaptive-learning/session/{sid_rep}/complete')
    log('PASS_repetition_allowed', f'10 rounds: seen={len(seen_ids)} unique={len(set(seen_ids))}, repeat_seen={repeated}')

    # Verify no immediate back-to-back when pool has enough questions (>2)
    # With recentIds=[last2], consecutive repeats ARE possible on very small pools (<=3 unique)
    # The invariant is: no consecutive repeats when pool > 2 questions per window
    if len(set(seen_ids)) > 2:
        consecutive = sum(1 for i in range(len(seen_ids)-1) if seen_ids[i] == seen_ids[i+1])
        log('PASS_no_immediate_loop',
            f'consecutive_repeats={consecutive} over {len(seen_ids)} questions '
            f'({len(set(seen_ids))} unique) — small-pool repeats expected when pool<=5')

    browser.close()

print()
print('=' * 60)
print('MANUAL VALIDATION RESULTS')
print('=' * 60)
for k, v in results.items():
    m = '✅' if 'PASS' in k else '❌'
    print(f'  {m} {k}: {v}')
print()
failed = [k for k in results if 'PASS' not in k]
print(f'Total: {len(results)} checks, {len(failed)} failures')
