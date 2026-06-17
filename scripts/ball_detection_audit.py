"""
ball_detection_audit.py — AN-3B2C-1 Validation & Quality Audit

Read-only DB audit for the juggling ball detection pipeline.
Queries juggling_ball_detections and outputs aggregated quality metrics
as a Markdown report. No personal data is exported: no user IDs,
video file paths, or raw per-event coordinates appear in the output.

Quality thresholds (gate for AN-3B2C-2 pitch calibration):
  detection_success_rate  >= 70%   (auto pipeline found ball)
  pct_within_loose        >= 65%   (correction distance < 5% of frame width)
  fp_rate                 <= 15%   (auto-detected ball later marked as no-ball)
  |bias_dx|               <  3%    (no systematic horizontal drift)
  |bias_dy|               <  3%    (no systematic vertical drift)

Minimum sample requirements:
  total audit events      >= 200
  per training_video_type >= 50

FP rates — two metrics:
  fp_rate_overall: proxy (auto_ball_x IS NOT NULL denominator); always available.
  fp_rate_high_conf: precise (auto_confidence >= 0.80 denominator, migration 2026_06_18_1400).
  Pre-migration rows show N/A for fp_rate_high_conf; fp_rate_overall is the gate threshold.

Usage:
    DATABASE_URL=postgresql://... python scripts/ball_detection_audit.py
    DATABASE_URL=postgresql://... python scripts/ball_detection_audit.py --exit-code
    DATABASE_URL=postgresql://... python scripts/ball_detection_audit.py --since-days 30

Exit codes:
    0  All thresholds met — proceed to pitch calibration (AN-3B2C-2)
    1  One or more thresholds not met — review before proceeding
    2  Insufficient data (< minimum sample requirements)
    3  Database connection error or table not found
"""
from __future__ import annotations

import argparse
import math
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Any

try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
except ImportError:
    print("ERROR: psycopg2 not installed. Run: pip install psycopg2-binary", file=sys.stderr)
    sys.exit(3)


# ── CLI ────────────────────────────────────────────────────────────────────────

_parser = argparse.ArgumentParser(
    description="AN-3B2C-1 ball detection quality audit (read-only)"
)
_parser.add_argument(
    "--exit-code", action="store_true",
    help="Exit 1 when thresholds are not met, 2 when data is insufficient"
)
_parser.add_argument(
    "--since-days", type=int, default=None,
    help="Only include detections created in the last N days (default: all time)"
)
args = _parser.parse_args()


# ── Thresholds ─────────────────────────────────────────────────────────────────

DETECTION_SUCCESS_RATE_MIN = 0.70
PCT_WITHIN_LOOSE_MIN       = 0.65
FP_RATE_MAX                = 0.15
BIAS_ABS_MAX               = 0.03
MIN_TOTAL_EVENTS           = 200
MIN_EVENTS_PER_TYPE        = 50

STRICT_CD                  = 0.03   # < 3% of frame width
LOOSE_CD                   = 0.05   # < 5% of frame width


# ── Statistics helpers ─────────────────────────────────────────────────────────

def _percentile(sv: list[float], p: float) -> float:
    """p ∈ [0, 100]; sv must be sorted ascending."""
    if not sv:
        return float("nan")
    n   = len(sv)
    idx = (p / 100.0) * (n - 1)
    lo  = int(idx)
    hi  = min(lo + 1, n - 1)
    return sv[lo] * (1.0 - (idx - lo)) + sv[hi] * (idx - lo)


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else float("nan")


def _safe_div(num: int | float, den: int | float) -> float:
    return float("nan") if den == 0 else num / den


def _fmt(v: float, decimals: int = 4) -> str:
    return "N/A" if math.isnan(v) else f"{v:.{decimals}f}"


def _pct(v: float) -> str:
    return "N/A" if math.isnan(v) else f"{v * 100:.1f}%"


def _badge(ok: bool | None) -> str:
    if ok is None:
        return "—"
    return "✓ PASS" if ok else "✗ FAIL"


def _cd_stats(corrections: list[dict]) -> dict:
    """Compute correction distance statistics from a list of {dx, dy, cd} dicts."""
    if not corrections:
        return {
            "n":             0,
            "mean_cd":       float("nan"),
            "median_cd":     float("nan"),
            "p90_cd":        float("nan"),
            "pct_strict":    float("nan"),
            "pct_loose":     float("nan"),
            "bias_dx":       float("nan"),
            "bias_dy":       float("nan"),
        }
    cds  = sorted(r["cd"] for r in corrections)
    dxs  = [r["dx"] for r in corrections]
    dys  = [r["dy"] for r in corrections]
    n    = len(cds)
    return {
        "n":             n,
        "mean_cd":       _mean(cds),
        "median_cd":     _percentile(cds, 50),
        "p90_cd":        _percentile(cds, 90),
        "pct_strict":    sum(1 for c in cds if c < STRICT_CD) / n,
        "pct_loose":     sum(1 for c in cds if c < LOOSE_CD)  / n,
        "bias_dx":       _mean(dxs),
        "bias_dy":       _mean(dys),
    }


# ── Markdown helpers ───────────────────────────────────────────────────────────

def _md_table(headers: list[str], rows: list[list[str]]) -> str:
    widths = [
        max(len(h), max((len(str(r[i])) for r in rows), default=0))
        for i, h in enumerate(headers)
    ]
    sep  = "| " + " | ".join("-" * w for w in widths) + " |"
    head = "| " + " | ".join(h.ljust(widths[i]) for i, h in enumerate(headers)) + " |"
    body = "\n".join(
        "| " + " | ".join(str(c).ljust(widths[i]) for i, c in enumerate(row)) + " |"
        for row in rows
    )
    return f"{head}\n{sep}\n{body}"


# ── SQL ────────────────────────────────────────────────────────────────────────

def _date_filter(alias: str = "jbd", since: datetime | None = None) -> str:
    if since is None:
        return ""
    return f"AND {alias}.created_at >= '{since.isoformat()}'"


_OVERALL_SQL = """
SELECT
    COUNT(*)                                                                    AS total,
    COUNT(*) FILTER (WHERE detection_source = 'mobilenet_ssd_v1'
                       AND no_ball_detected = false)                            AS auto_ball_found,
    COUNT(*) FILTER (WHERE detection_source = 'mobilenet_ssd_v1'
                       AND no_ball_detected = true)                             AS auto_no_ball,
    COUNT(*) FILTER (WHERE detection_source = 'manual')                        AS manual_total,
    COUNT(*) FILTER (WHERE detection_source = 'manual'
                       AND no_ball_detected = true
                       AND auto_ball_x IS NOT NULL)                            AS fp_manual_no_ball,
    COUNT(*) FILTER (WHERE auto_ball_x IS NOT NULL)                            AS has_auto_coords,
    -- High-confidence FP (auto_confidence >= 0.80, AN-3B2C-1 follow-up)
    COUNT(*) FILTER (WHERE detection_source = 'manual'
                       AND no_ball_detected = true
                       AND auto_confidence >= 0.80)                           AS fp_hc_no_ball,
    COUNT(*) FILTER (WHERE auto_confidence >= 0.80)                           AS hc_auto_count
FROM juggling_ball_detections
WHERE TRUE {date_filter}
"""

_CORRECTION_SQL = """
SELECT
    ball_x  - auto_ball_x                                                       AS dx,
    ball_y  - auto_ball_y                                                       AS dy,
    sqrt(power(ball_x - auto_ball_x, 2) + power(ball_y - auto_ball_y, 2))     AS cd
FROM juggling_ball_detections
WHERE detection_source = 'manual'
  AND auto_ball_x IS NOT NULL
  AND auto_ball_y IS NOT NULL
  AND ball_x IS NOT NULL
  AND ball_y IS NOT NULL
  {date_filter}
"""

_CONF_HIST_SQL = """
SELECT
    CASE
        WHEN confidence >= 0.90 THEN '[0.90–1.00]'
        WHEN confidence >= 0.80 THEN '[0.80–0.90)'
        WHEN confidence >= 0.70 THEN '[0.70–0.80)'
        WHEN confidence >= 0.60 THEN '[0.60–0.70)'
        WHEN confidence >= 0.50 THEN '[0.50–0.60)'
        WHEN confidence >= 0.40 THEN '[0.40–0.50)'
        WHEN confidence >= 0.30 THEN '[0.30–0.40)'
        WHEN confidence >= 0.20 THEN '[0.20–0.30)'
        WHEN confidence >= 0.10 THEN '[0.10–0.20)'
        ELSE                         '[0.00–0.10)'
    END                                   AS band,
    COUNT(*)                              AS n,
    ROUND(100.0 * COUNT(*) / NULLIF(SUM(COUNT(*)) OVER (), 0), 1) AS pct
FROM juggling_ball_detections
WHERE detection_source = 'mobilenet_ssd_v1'
  AND confidence IS NOT NULL
  {date_filter}
GROUP BY band
ORDER BY MIN(confidence) DESC
"""

_PER_TYPE_OVERALL_SQL = """
SELECT
    jv.training_video_type                                                       AS vtype,
    COUNT(jbd.id)                                                                AS total,
    COUNT(*) FILTER (WHERE jbd.detection_source = 'mobilenet_ssd_v1'
                       AND jbd.no_ball_detected = false)                         AS auto_ball_found,
    COUNT(*) FILTER (WHERE jbd.no_ball_detected = true)                         AS no_ball_total,
    COUNT(*) FILTER (WHERE jbd.detection_source = 'manual'
                       AND jbd.no_ball_detected = true
                       AND jbd.auto_ball_x IS NOT NULL)                         AS fp_manual_no_ball,
    COUNT(*) FILTER (WHERE jbd.auto_ball_x IS NOT NULL)                         AS has_auto_coords
FROM juggling_ball_detections jbd
JOIN juggling_videos jv ON jv.id = jbd.video_id
WHERE TRUE {date_filter}
GROUP BY jv.training_video_type
ORDER BY jv.training_video_type
"""

_PER_TYPE_CORRECTIONS_SQL = """
SELECT
    jv.training_video_type                                                       AS vtype,
    jbd.ball_x  - jbd.auto_ball_x                                               AS dx,
    jbd.ball_y  - jbd.auto_ball_y                                               AS dy,
    sqrt(power(jbd.ball_x - jbd.auto_ball_x, 2)
       + power(jbd.ball_y - jbd.auto_ball_y, 2))                               AS cd
FROM juggling_ball_detections jbd
JOIN juggling_videos jv ON jv.id = jbd.video_id
WHERE jbd.detection_source = 'manual'
  AND jbd.auto_ball_x IS NOT NULL
  AND jbd.auto_ball_y IS NOT NULL
  AND jbd.ball_x IS NOT NULL
  AND jbd.ball_y IS NOT NULL
  {date_filter}
ORDER BY jv.training_video_type
"""


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> int:
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("ERROR: DATABASE_URL environment variable not set.", file=sys.stderr)
        return 3

    since: datetime | None = None
    if args.since_days is not None:
        since = datetime.now(tz=timezone.utc) - timedelta(days=args.since_days)

    date_filter_bare   = f"AND created_at >= '{since.isoformat()}'"   if since else ""
    date_filter_jbd    = f"AND jbd.created_at >= '{since.isoformat()}'" if since else ""
    date_filter_where  = f"WHERE created_at >= '{since.isoformat()}'"  if since else ""

    try:
        conn = psycopg2.connect(db_url)
        conn.set_session(readonly=True, autocommit=True)
    except Exception as exc:
        print(f"ERROR: Cannot connect to database: {exc}", file=sys.stderr)
        return 3

    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:

            # ── 1. Overall totals ──────────────────────────────────────────────
            try:
                cur.execute(_OVERALL_SQL.format(date_filter=date_filter_bare))
            except psycopg2.errors.UndefinedTable:
                print(
                    "ERROR: juggling_ball_detections table not found. "
                    "Run alembic upgrade head first.",
                    file=sys.stderr,
                )
                return 3
            overall = dict(cur.fetchone())

            total          = int(overall["total"])
            auto_found     = int(overall["auto_ball_found"])
            auto_no_ball   = int(overall["auto_no_ball"])
            manual_total   = int(overall["manual_total"])
            fp_manual_nob  = int(overall["fp_manual_no_ball"])
            has_auto       = int(overall["has_auto_coords"])
            fp_hc_nob      = int(overall["fp_hc_no_ball"])
            hc_auto        = int(overall["hc_auto_count"])

            # Denominator for auto-detection events: auto_found + auto_no_ball
            auto_attempts  = auto_found + auto_no_ball
            success_rate   = _safe_div(auto_found, auto_attempts)

            # fp_rate_overall: proxy — auto-detected ball → annotator corrected to no-ball.
            # Denominator: all rows where auto_ball_x IS NOT NULL (auto once found a ball).
            fp_rate_overall = _safe_div(fp_manual_nob, has_auto)

            # fp_rate_high_conf: precise — requires auto_confidence >= 0.80 (AN-3B2C-1 follow-up).
            # N/A (nan) when no rows have auto_confidence populated (pre-migration data).
            fp_rate_hc      = _safe_div(fp_hc_nob, hc_auto)

            no_ball_rate    = _safe_div(auto_no_ball + fp_manual_nob, total)

            # ── 2. Correction distances ────────────────────────────────────────
            cur.execute(_CORRECTION_SQL.format(date_filter=date_filter_bare))
            corr_rows = [{"dx": float(r["dx"]), "dy": float(r["dy"]), "cd": float(r["cd"])}
                         for r in cur.fetchall()]
            cd = _cd_stats(corr_rows)

            # ── 3. Confidence distribution ─────────────────────────────────────
            cur.execute(_CONF_HIST_SQL.format(date_filter=date_filter_bare))
            conf_hist = [dict(r) for r in cur.fetchall()]

            # ── 4. Per-type overall ────────────────────────────────────────────
            cur.execute(_PER_TYPE_OVERALL_SQL.format(date_filter=date_filter_jbd))
            per_type_rows = {r["vtype"]: dict(r) for r in cur.fetchall()}

            cur.execute(_PER_TYPE_CORRECTIONS_SQL.format(date_filter=date_filter_jbd))
            per_type_corr: dict[str, list[dict]] = {}
            for r in cur.fetchall():
                vt = r["vtype"]
                per_type_corr.setdefault(vt, []).append(
                    {"dx": float(r["dx"]), "dy": float(r["dy"]), "cd": float(r["cd"])}
                )

    finally:
        conn.close()

    # ── Evaluate thresholds ────────────────────────────────────────────────────

    insufficient_data = total < MIN_TOTAL_EVENTS

    t_success   = None if math.isnan(success_rate)     else success_rate     >= DETECTION_SUCCESS_RATE_MIN
    t_loose     = None if math.isnan(cd["pct_loose"])  else cd["pct_loose"]  >= PCT_WITHIN_LOOSE_MIN
    # Gate on fp_rate_overall (always computable once data exists).
    # fp_rate_hc is displayed for information but not used as a hard gate (pre-migration rows are N/A).
    t_fp        = None if math.isnan(fp_rate_overall)  else fp_rate_overall  <= FP_RATE_MAX
    t_bias_dx   = None if math.isnan(cd["bias_dx"])    else abs(cd["bias_dx"]) < BIAS_ABS_MAX
    t_bias_dy   = None if math.isnan(cd["bias_dy"])    else abs(cd["bias_dy"]) < BIAS_ABS_MAX

    all_pass = all(
        v is True for v in [t_success, t_loose, t_fp, t_bias_dx, t_bias_dy]
        if v is not None
    )

    # ── Per-type sample warnings ───────────────────────────────────────────────

    type_warnings: list[str] = []
    for vtype in ["juggling", "gan_footvolley", "gan_foottennis"]:
        n = int(per_type_rows.get(vtype, {}).get("total", 0))
        if n < MIN_EVENTS_PER_TYPE:
            type_warnings.append(f"{vtype}: {n} events (< {MIN_EVENTS_PER_TYPE} required)")

    # ── Output ─────────────────────────────────────────────────────────────────

    now_str = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    window_str = (
        f"last {args.since_days} days"
        if args.since_days
        else "all time"
    )

    print(f"\n# AN-3B2C-1 Ball Detection Quality Audit")
    print(f"\n**Generated:** {now_str}  ")
    print(f"**Window:** {window_str}  ")
    print(f"**Total events in scope:** {total}")

    if insufficient_data:
        print(
            f"\n> ⚠️  INSUFFICIENT DATA — {total} events found, "
            f"{MIN_TOTAL_EVENTS} required. Results shown for reference only.\n"
        )
    if type_warnings:
        print("> **Per-type sample warnings:**")
        for w in type_warnings:
            print(f">   - {w}")
        print()

    # Section 1: Detection success rate
    print("\n## 1. Detection Success Rate\n")
    print(_md_table(
        ["Metric",                        "Value",                    "Threshold",            "Status"],
        [
            ["Total detections",          str(total),                 "—",                    "—"],
            ["Auto pipeline events",      str(auto_attempts),         "—",                    "—"],
            ["Auto found ball",           str(auto_found),            "—",                    "—"],
            ["Auto no-ball",              str(auto_no_ball),          "—",                    "—"],
            ["Manual corrections",        str(manual_total),          "—",                    "—"],
            ["Detection success rate",    _pct(success_rate),         f">= {DETECTION_SUCCESS_RATE_MIN*100:.0f}%", _badge(t_success)],
            ["No-ball rate (overall)",    _pct(no_ball_rate),         "context only",         "—"],
        ],
    ))

    # Section 2: Correction distances
    print("\n## 2. Correction Distance (Auto → Manual)\n")
    print(f"*N corrections with frozen auto coords: {cd['n']}*\n")
    print(_md_table(
        ["Metric",                   "Value",                    "Threshold",                    "Status"],
        [
            ["Mean CD (normalized)", _fmt(cd["mean_cd"]),        "< 0.05 (target)",              "—"],
            ["Median CD",            _fmt(cd["median_cd"]),      "< 0.04 (target)",              "—"],
            ["P90 CD",               _fmt(cd["p90_cd"]),         "< 0.10 (target)",              "—"],
            ["% within strict 3%",   _pct(cd["pct_strict"]),     f"> 60% (target)",              "—"],
            ["% within loose 5%",    _pct(cd["pct_loose"]),      f">= {PCT_WITHIN_LOOSE_MIN*100:.0f}%",
             _badge(t_loose)],
            ["Bias dx (mean)",       _fmt(cd["bias_dx"]),        f"|dx| < {BIAS_ABS_MAX:.2f}",   _badge(t_bias_dx)],
            ["Bias dy (mean)",       _fmt(cd["bias_dy"]),        f"|dy| < {BIAS_ABS_MAX:.2f}",   _badge(t_bias_dy)],
        ],
    ))
    print(
        "\n*CD = euclidean distance in normalized [0,1] frame space. "
        "5% ≈ 54 px on 1080p.*"
    )

    # Section 3: False positive rate
    print("\n## 3. False Positive Rate\n")
    hc_note = (
        "*High-confidence FP: N/A — no rows with auto_confidence populated yet "
        "(pre-migration data or auto pipeline not yet run).*"
        if hc_auto == 0 else
        f"*High-confidence FP uses auto_confidence >= 0.80 "
        f"({hc_auto} eligible rows).*"
    )
    print(hc_note + "\n")
    print(_md_table(
        ["Metric",                                    "Value",                  "Threshold",                         "Status"],
        [
            ["Events with frozen auto coords",        str(has_auto),            "—",                                 "—"],
            ["Auto→manual corrected to no-ball",      str(fp_manual_nob),       "—",                                 "—"],
            ["fp_rate_overall (proxy)",               _pct(fp_rate_overall),    f"<= {FP_RATE_MAX*100:.0f}%",        _badge(t_fp)],
            ["fp_rate_high_conf (>= 0.80 original)",  _pct(fp_rate_hc),         f"<= {FP_RATE_MAX*100:.0f}% (info)", "—"],
        ],
    ))
    print(
        "\n*fp_rate_overall: denominator = rows where auto_ball_x IS NOT NULL. "
        "fp_rate_high_conf: precise, requires auto_confidence column (migration 2026_06_18_1400).*"
    )

    # Section 4: Confidence distribution
    print("\n## 4. Confidence Distribution (Auto Detections)\n")
    if conf_hist:
        print(_md_table(
            ["Confidence Band",  "Count",                  "% of Auto"],
            [
                [r["band"], str(r["n"]), f"{r['pct']:.1f}%"]
                for r in conf_hist
            ],
        ))
    else:
        print("*No auto detections with confidence data found.*")

    # Section 5: Per-type breakdown
    print("\n## 5. Per Training Video Type\n")

    type_table_rows: list[list[str]] = []
    for vtype in ["juggling", "gan_footvolley", "gan_foottennis"]:
        pt = per_type_rows.get(vtype)
        if pt is None:
            type_table_rows.append([vtype, "0", "—", "—", "—", "—", "—", "—"])
            continue
        n_total     = int(pt["total"])
        n_auto_ok   = int(pt["auto_ball_found"])
        n_auto_att  = n_auto_ok + int(pt.get("no_ball_total", 0))
        sr          = _safe_div(n_auto_ok, n_auto_att)
        fp_n        = int(pt["fp_manual_no_ball"])
        ha          = int(pt["has_auto_coords"])
        fpr         = _safe_div(fp_n, ha)

        pt_corr     = per_type_corr.get(vtype, [])
        pt_cd       = _cd_stats(pt_corr)
        warn        = " ⚠️" if n_total < MIN_EVENTS_PER_TYPE else ""

        type_table_rows.append([
            f"{vtype}{warn}",
            str(n_total),
            _pct(sr),
            _pct(fpr),
            _fmt(pt_cd["mean_cd"]),
            _pct(pt_cd["pct_loose"]),
            _fmt(pt_cd["bias_dx"]),
            _fmt(pt_cd["bias_dy"]),
        ])

    print(_md_table(
        ["Type", "Events", "Success Rate", "FP Rate", "Mean CD", "% Loose", "Bias dx", "Bias dy"],
        type_table_rows,
    ))
    print(f"\n*⚠️ = fewer than {MIN_EVENTS_PER_TYPE} events — results not statistically reliable*")

    # Section 6: Threshold summary
    print("\n## 6. Go/No-Go Summary\n")
    print(_md_table(
        ["Threshold",                    "Required",                         "Actual",               "Status"],
        [
            ["Detection success rate",   f">= {DETECTION_SUCCESS_RATE_MIN*100:.0f}%",
             _pct(success_rate),         _badge(t_success)],
            ["% within loose (CD<5%)",   f">= {PCT_WITHIN_LOOSE_MIN*100:.0f}%",
             _pct(cd["pct_loose"]),      _badge(t_loose)],
            ["FP rate overall (proxy)",   f"<= {FP_RATE_MAX*100:.0f}%",
             _pct(fp_rate_overall),      _badge(t_fp)],
            ["|bias_dx|",               f"< {BIAS_ABS_MAX:.2f}",
             _fmt(cd["bias_dx"]),        _badge(t_bias_dx)],
            ["|bias_dy|",               f"< {BIAS_ABS_MAX:.2f}",
             _fmt(cd["bias_dy"]),        _badge(t_bias_dy)],
        ],
    ))

    if insufficient_data:
        print(
            f"\n**VERDICT: INSUFFICIENT DATA** — "
            f"collect at least {MIN_TOTAL_EVENTS} audit events before evaluating thresholds."
        )
    elif all_pass:
        print("\n**VERDICT: ALL THRESHOLDS MET** — system ready for AN-3B2C-2 pitch calibration.")
    else:
        failed = [
            name for name, ok in [
                ("detection_success_rate", t_success),
                ("pct_within_loose",       t_loose),
                ("fp_rate_overall",        t_fp),
                ("bias_dx",                t_bias_dx),
                ("bias_dy",                t_bias_dy),
            ] if ok is False
        ]
        print(
            f"\n**VERDICT: NOT READY** — "
            f"thresholds not met: {', '.join(failed)}. "
            f"Review model performance before AN-3B2C-2."
        )

    print()

    if not args.exit_code:
        return 0
    if insufficient_data:
        return 2
    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
