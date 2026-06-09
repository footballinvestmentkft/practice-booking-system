"""
Biometric Accuracy Benchmark — R&D validation script.

Evaluates face recognition accuracy using real SCRFD + AdaFace embeddings.

Pair taxonomy:
  Genuine  : two images of the SAME person   (intra-identity)
  Impostor : two images of DIFFERENT persons (cross-identity)

Outputs:
  1. Score distributions (mean, std, percentiles, d-prime)
  2. Confusion matrix  (genuine/impostor × verified/manual_review/rejected)
  3. Outcome rates     (TAR, FRR, FAR, TIR, MRR-G, MRR-I)
  4. Threshold sweep   (FAR/FRR table, EER, TAR@FAR=0.1/1/5/10%)
  5. Per-pair listing  (use --no-per-pair to suppress)
  6. Recommendations

Usage:
  python scripts/biometric_accuracy_benchmark.py \\
    --identity person_a_session1.jpg person_a_session2.jpg person_a_session3.jpg \\
    --identity person_b_session1.jpg person_b_session2.jpg \\
    --identity person_c_session1.jpg person_c_session2.jpg \\
    --model /path/to/adaface_ir50_webface4m.onnx \\
    --detector /path/to/det_500m.onnx

Each --identity flag = one person; its images should be from different captures
(different sessions, lighting, angles).  Requires ≥2 identities, ≥2 images each.
Recommended: ≥5 identities, ≥3 images per identity for meaningful FAR/FRR estimates.

IMPORTANT — R&D rules:
  - Scores printed to console only — never to API, UI, or any log file
  - Model and test images must NOT be committed to the repository
  - BIOMETRIC_ONNX_RND_ENABLED=true is an R&D-only flag; forbidden in production
  - DPIA, DPO sign-off, bias/fairness audit required before production use
"""
from __future__ import annotations

import argparse
import math
import os
import sys
from pathlib import Path
from typing import NamedTuple

# ── Python path ────────────────────────────────────────────────────────────────
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


# ── R&D env setup — MUST happen before any app.config import ──────────────────
# Pydantic BaseSettings reads env at instantiation time; setting vars here
# (module load, before any app import) ensures all modules see correct values.

def _argv_value(flag: str) -> str | None:
    for i, arg in enumerate(sys.argv):
        if arg == flag and i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return None


os.environ.setdefault("BIOMETRIC_FACE_MATCHING_ENABLED",     "true")
os.environ.setdefault("BIOMETRIC_EMBEDDING_PROVIDER",        "onnx")
os.environ.setdefault("BIOMETRIC_ONNX_RND_ENABLED",          "true")
os.environ.setdefault("BIOMETRIC_ENCRYPTION_ALLOW_TEST_KEY", "true")
os.environ.setdefault("BIOMETRIC_EMBEDDING_KEY",             "")
os.environ.setdefault("BIOMETRIC_DISCLOSURE_ENABLED",        "true")

for _flag, _env in [
    ("--model",          "BIOMETRIC_ONNX_MODEL_PATH"),
    ("--sha256",         "BIOMETRIC_ONNX_MODEL_SHA256"),
    ("--detector",       "BIOMETRIC_FACE_DETECTOR_PATH"),
    ("--detector-sha256","BIOMETRIC_FACE_DETECTOR_SHA256"),
]:
    _val = _argv_value(_flag)
    if _val:
        os.environ[_env] = _val


# ── Thresholds (mirrors matching_service.py — keep in sync) ───────────────────
MATCH_THRESHOLD = 0.75   # score >= MATCH_THRESHOLD → verified
REVIEW_LOWER    = 0.55   # REVIEW_LOWER <= score < MATCH_THRESHOLD → manual_review
                          # score < REVIEW_LOWER → rejected


# ── Data structures ────────────────────────────────────────────────────────────

class ScorePair(NamedTuple):
    identity_i: int
    img_i:      int
    identity_j: int
    img_j:      int
    score:      float
    is_genuine: bool   # True = same identity, False = different identity


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


def _stats(values: list[float]) -> dict:
    if not values:
        return {k: float("nan") for k in ("n","mean","std","min","p10","p25","p50","p75","p90","max")}
    sv   = sorted(values)
    n    = len(sv)
    mean = sum(sv) / n
    std  = math.sqrt(sum((v - mean) ** 2 for v in sv) / n)
    return {
        "n":    n,
        "mean": mean,
        "std":  std,
        "min":  sv[0],
        "p10":  _percentile(sv, 10),
        "p25":  _percentile(sv, 25),
        "p50":  _percentile(sv, 50),
        "p75":  _percentile(sv, 75),
        "p90":  _percentile(sv, 90),
        "max":  sv[-1],
    }


def _dprime(s_genuine: dict, s_impostor: dict) -> float:
    """d' (discriminability) — pooled-std version."""
    pooled_var = (s_genuine["std"] ** 2 + s_impostor["std"] ** 2) / 2.0
    pooled_std = math.sqrt(pooled_var) if pooled_var > 0 else 1e-9
    return (s_genuine["mean"] - s_impostor["mean"]) / pooled_std


# ── Outcome helper ─────────────────────────────────────────────────────────────

def _outcome(score: float) -> str:
    if score >= MATCH_THRESHOLD:
        return "verified"
    if score >= REVIEW_LOWER:
        return "manual_review"
    return "rejected"


# ── Output helpers ─────────────────────────────────────────────────────────────

W = 72   # output width

def _section(title: str) -> None:
    pad = max(0, W - 4 - len(title))
    print(f"\n── {title} {'─' * pad}")


def _pct(num: int, denom: int, width: int = 7) -> str:
    if denom == 0:
        return " " * (width - 3) + "n/a"
    return f"{100.0 * num / denom:{width}.1f}%"


def _close(a: float, b: float, eps: float = 0.005) -> bool:
    return abs(a - b) < eps


# ── Embedding + score collection ───────────────────────────────────────────────

def collect_scores(
    identities: list[list[bytes]],
    provider,
) -> list[ScorePair]:
    """
    Generate embeddings for all images; compute all pairwise cosine similarities.

    Genuine  pairs: (id_i, img_a) vs (id_i, img_b) for all a < b within identity i
    Impostor pairs: (id_i, img_a) vs (id_j, img_b) for all i < j across identities

    Alignment failures and other embedding errors are caught and skipped;
    affected pairs are excluded from analysis (not treated as low-score matches).
    """
    from app.services.biometric.matching_service import compute_cosine_similarity

    print("\n  Generating embeddings...")
    embed_grid: list[list[list[float] | None]] = []
    skip_count = 0

    for id_idx, images in enumerate(identities):
        row: list[list[float] | None] = []
        for img_idx, img_bytes in enumerate(images):
            try:
                emb = provider.generate(img_bytes)
                row.append(emb)
                print(f"    id[{id_idx}] img[{img_idx}] ✅  embedded  (dim={len(emb)})")
            except Exception as exc:
                row.append(None)
                skip_count += 1
                # Sanitised: error type + code only, no image data
                exc_name = type(exc).__name__
                exc_detail = getattr(exc, "code", None) or getattr(exc, "args", ("",))[0]
                safe_detail = str(exc_detail)[:60] if isinstance(exc_detail, str) else exc_name
                print(f"    id[{id_idx}] img[{img_idx}] ⚠️   SKIPPED — {exc_name}: {safe_detail}")
        embed_grid.append(row)

    if skip_count:
        print(f"\n  ⚠️  {skip_count} image(s) skipped; affected pairs excluded from analysis")

    print("\n  Computing pairwise scores...")
    pairs: list[ScorePair] = []
    n_ids = len(identities)

    for i in range(n_ids):
        for j in range(i, n_ids):
            is_genuine = (i == j)
            imgs_i = embed_grid[i]
            imgs_j = embed_grid[j]

            for xi in range(len(imgs_i)):
                xj_start = xi + 1 if is_genuine else 0
                for xj in range(xj_start, len(imgs_j)):
                    emb_i = imgs_i[xi]
                    emb_j = imgs_j[xj]
                    if emb_i is None or emb_j is None:
                        continue
                    score = compute_cosine_similarity(emb_i, emb_j)
                    pairs.append(ScorePair(
                        identity_i=i, img_i=xi,
                        identity_j=j, img_j=xj,
                        score=score,
                        is_genuine=is_genuine,
                    ))

    return pairs


# ── Report sections ────────────────────────────────────────────────────────────

def report_score_distributions(
    genuine_scores: list[float],
    impostor_scores: list[float],
) -> None:
    _section("1. Score Distributions")

    sg = _stats(genuine_scores)
    sd = _stats(impostor_scores)

    hdr = f"  {'':13s}│{'n':>5s} │{'mean':>7s} │{'std':>7s} │{'min':>7s} │{'p25':>7s} │{'p50':>7s} │{'p75':>7s} │{'max':>7s}"
    sep = "  " + "─" * 13 + "┼" + ("───────┼" * 7) + "───────"
    print(hdr)
    print(sep)

    for label, s in [("Genuine", sg), ("Impostor", sd)]:
        if math.isnan(s["n"]):
            print(f"  {label:<13s}│  (no data)")
            continue
        print(
            f"  {label:<13s}│"
            f"{int(s['n']):>5d} │"
            f"{s['mean']:>7.4f} │"
            f"{s['std']:>7.4f} │"
            f"{s['min']:>7.4f} │"
            f"{s['p25']:>7.4f} │"
            f"{s['p50']:>7.4f} │"
            f"{s['p75']:>7.4f} │"
            f"{s['max']:>7.4f}"
        )

    if genuine_scores and impostor_scores:
        margin  = sg["mean"] - sd["mean"]
        dp      = _dprime(sg, sd)
        overlap = sum(1 for s in impostor_scores if s >= sg["min"]) / len(impostor_scores)
        print(f"\n  Separation margin  (genuine_mean − impostor_mean) : {margin:+.4f}")
        print(f"  d-prime            (discriminability)              : {dp:.3f}")
        print(f"  Impostor overlap   (impostors ≥ genuine min score) : {overlap*100:.1f}%")

        if dp >= 3.0:
            print("  → d' ≥ 3.0 — excellent discriminability")
        elif dp >= 2.0:
            print("  → d' ≥ 2.0 — good discriminability")
        elif dp >= 1.0:
            print("  → d' ≥ 1.0 — moderate discriminability")
        else:
            print("  → d' < 1.0 — poor discriminability (distributions overlap heavily)")

    # Score histogram (ASCII, 20 bins)
    _print_histogram(genuine_scores, impostor_scores)


def _print_histogram(genuine: list[float], impostor: list[float], bins: int = 20) -> None:
    if not genuine or not impostor:
        return
    lo, hi = -0.2, 1.0
    step = (hi - lo) / bins
    bar_max = 24

    g_hist = [0] * bins
    d_hist = [0] * bins
    for s in genuine:
        idx = min(int((s - lo) / step), bins - 1)
        if 0 <= idx < bins:
            g_hist[idx] += 1
    for s in impostor:
        idx = min(int((s - lo) / step), bins - 1)
        if 0 <= idx < bins:
            d_hist[idx] += 1

    max_count = max(max(g_hist), max(d_hist), 1)
    scale = bar_max / max_count

    print(f"\n  Score histogram  (G=genuine █, I=impostor ░):")
    print(f"  {'score':>7s}  {'':28s}  count")
    for i in range(bins):
        centre = lo + (i + 0.5) * step
        g_bar  = "█" * int(g_hist[i] * scale)
        d_bar  = "░" * int(d_hist[i] * scale)
        # Mark threshold lines
        flag = ""
        if _close(centre, REVIEW_LOWER, step / 2):
            flag = " ← REVIEW_LOWER"
        elif _close(centre, MATCH_THRESHOLD, step / 2):
            flag = " ← MATCH_THRESHOLD"
        print(f"  {centre:>6.2f}   G:{g_bar:<{bar_max}s}  {g_hist[i]:3d}")
        if d_hist[i] > 0:
            print(f"  {'':>6s}   I:{d_bar:<{bar_max}s}  {d_hist[i]:3d}{flag}")
        elif flag:
            print(f"  {'':>6s}   {'':3s}{flag}")


def report_confusion_matrix(pairs: list[ScorePair]) -> dict:
    _section(f"2. Confusion Matrix  (reject<{REVIEW_LOWER}≤review<{MATCH_THRESHOLD}≤verified)")

    counts: dict[str, dict[str, int]] = {
        "genuine":  {"verified": 0, "manual_review": 0, "rejected": 0},
        "impostor": {"verified": 0, "manual_review": 0, "rejected": 0},
    }
    for p in pairs:
        truth = "genuine" if p.is_genuine else "impostor"
        counts[truth][_outcome(p.score)] += 1

    g  = counts["genuine"]
    d  = counts["impostor"]
    ng = sum(g.values())
    ni = sum(d.values())

    cw = 16
    print(f"\n  {'':17s}│{'verified':>{cw}s}│{'manual_review':>{cw}s}│{'rejected':>{cw}s}│{'Total':>7s}")
    print(f"  {'─' * 17}┼{'─' * cw}┼{'─' * cw}┼{'─' * cw}┼{'─' * 7}")
    print(f"  {'Genuine pairs':<17s}│{g['verified']:>{cw}d}│{g['manual_review']:>{cw}d}│{g['rejected']:>{cw}d}│{ng:>7d}")
    print(f"  {'Impostor pairs':<17s}│{d['verified']:>{cw}d}│{d['manual_review']:>{cw}d}│{d['rejected']:>{cw}d}│{ni:>7d}")

    return counts


def report_outcome_rates(counts: dict) -> None:
    _section("3. Outcome Rates")

    g  = counts["genuine"]
    d  = counts["impostor"]
    ng = sum(g.values())
    ni = sum(d.values())

    print(f"\n  Genuine pairs  (n={ng})")
    print(f"    TAR   — genuine → verified       {_pct(g['verified'],      ng)}")
    print(f"    MRR-G — genuine → manual_review  {_pct(g['manual_review'], ng)}")
    print(f"    FRR   — genuine → rejected        {_pct(g['rejected'],     ng)}")
    print(f"\n  Impostor pairs  (n={ni})")
    print(f"    FAR   — impostor → verified       {_pct(d['verified'],      ni)}")
    print(f"    MRR-I — impostor → manual_review  {_pct(d['manual_review'], ni)}")
    print(f"    TIR   — impostor → rejected        {_pct(d['rejected'],     ni)}")

    total      = ng + ni
    total_rev  = g["manual_review"] + d["manual_review"]
    print(f"\n  Overall manual_review rate                {_pct(total_rev, total)}")


def report_threshold_sensitivity(
    genuine_scores: list[float],
    impostor_scores: list[float],
) -> None:
    _section("4. Threshold Sensitivity  (binary: verified vs not-verified)")

    if not genuine_scores or not impostor_scores:
        print("  (insufficient data)")
        return

    ng = len(genuine_scores)
    ni = len(impostor_scores)

    # Sweep 0.00 → 1.00 in steps of 0.01
    thresholds = [t / 100 for t in range(0, 101)]

    sweep = []
    for t in thresholds:
        far = sum(1 for s in impostor_scores if s >= t) / ni
        frr = sum(1 for s in genuine_scores  if s <  t) / ng
        sweep.append((t, far, frr, 1.0 - frr))

    # EER: threshold where |FAR - FRR| is minimal
    eer_t, _ = min(((t, abs(far - frr)) for t, far, frr, _ in sweep), key=lambda x: x[1])
    eer_far  = next(far for t, far, frr, _ in sweep if t == eer_t)
    eer_frr  = next(frr for t, far, frr, _ in sweep if t == eer_t)
    eer_val  = (eer_far + eer_frr) / 2.0

    print(f"\n  EER ≈ {eer_val * 100:.2f}%  at decision threshold ≈ {eer_t:.2f}")
    print(f"        (FAR={eer_far*100:.1f}%, FRR={eer_frr*100:.1f}% at that threshold)")

    # TAR @ standard FAR operating points
    print(f"\n  TAR at standard FAR operating points:")
    for target_far in (0.001, 0.01, 0.05, 0.10):
        candidates = [(t, tar) for t, far, frr, tar in sweep if far <= target_far]
        if candidates:
            best_t, best_tar = max(candidates, key=lambda x: x[1])
            print(f"    TAR @ FAR={target_far*100:>5.1f}% :  {best_tar*100:5.1f}%   (threshold ≥ {best_t:.2f})")
        else:
            print(f"    TAR @ FAR={target_far*100:>5.1f}% :  n/a  (no threshold achieves this FAR with current data)")

    # Tabular sweep — current thresholds + EER + every 0.05 step
    key_t: set[float] = set()
    for i in range(0, 101, 5):
        key_t.add(i / 100)
    key_t.add(REVIEW_LOWER)
    key_t.add(MATCH_THRESHOLD)
    key_t.add(eer_t)

    print(f"\n  Sweep (selected thresholds — binary verdict = verified vs rest):")
    print(f"  {'threshold':>10s} │ {'FAR(%)':>8s} │ {'FRR(%)':>8s} │ {'TAR(%)':>8s} │ note")
    print(f"  {'─'*10}─┼─{'─'*8}─┼─{'─'*8}─┼─{'─'*8}─┼{'─'*24}")
    for t, far, frr, tar in sweep:
        if not any(_close(t, k) for k in key_t):
            continue
        note = ""
        if _close(t, REVIEW_LOWER):
            note = "← REVIEW_LOWER (current)"
        elif _close(t, MATCH_THRESHOLD):
            note = "← MATCH_THRESHOLD (current)"
        elif _close(t, eer_t):
            note = "← EER point"
        print(f"  {t:>10.2f} │ {far*100:>8.2f} │ {frr*100:>8.2f} │ {tar*100:>8.2f} │ {note}")


def report_per_pair_details(
    pairs: list[ScorePair],
    identity_labels: list[str],
) -> None:
    _section("5. Per-Pair Score Details")

    genuine_pairs  = [p for p in pairs if     p.is_genuine]
    impostor_pairs = [p for p in pairs if not p.is_genuine]

    def _fmt(p: ScorePair) -> str:
        outcome = _outcome(p.score)
        correct = (p.is_genuine and outcome == "verified") or \
                  (not p.is_genuine and outcome == "rejected")
        review  = (outcome == "manual_review")
        marker  = "✅" if correct else ("⚠️ " if review else "❌")
        i_lbl   = identity_labels[p.identity_i] if p.identity_i < len(identity_labels) else f"id{p.identity_i}"
        j_lbl   = identity_labels[p.identity_j] if p.identity_j < len(identity_labels) else f"id{p.identity_j}"
        return f"  {marker}  {i_lbl}[{p.img_i}] vs {j_lbl}[{p.img_j}]  score={p.score:.4f}  → {outcome}"

    if genuine_pairs:
        print(f"\n  Genuine pairs  ({len(genuine_pairs)}  — expected: verified):")
        for p in sorted(genuine_pairs, key=lambda x: -x.score):
            print(_fmt(p))

    if impostor_pairs:
        print(f"\n  Impostor pairs  ({len(impostor_pairs)}  — expected: rejected):")
        for p in sorted(impostor_pairs, key=lambda x: -x.score):
            print(_fmt(p))


def report_recommendations(
    genuine_scores: list[float],
    impostor_scores: list[float],
    counts: dict,
    n_identities: int,
    n_images_per_identity: list[int],
) -> None:
    _section("6. Recommendations")

    if not genuine_scores or not impostor_scores:
        print("  Insufficient data for recommendations.")
        return

    sg = _stats(genuine_scores)
    sd = _stats(impostor_scores)
    dp = _dprime(sg, sd)

    ng  = sum(counts["genuine"].values())
    ni  = sum(counts["impostor"].values())
    tar = counts["genuine"]["verified"]      / ng if ng > 0 else 0.0
    frr = counts["genuine"]["rejected"]      / ng if ng > 0 else 0.0
    far = counts["impostor"]["verified"]     / ni if ni > 0 else 0.0
    mrr_i = counts["impostor"]["manual_review"] / ni if ni > 0 else 0.0

    ok:   list[str] = []
    warn: list[str] = []
    fix:  list[str] = []

    # ── Discriminability ─────────────────────────────────────────────────────
    if dp >= 3.0:
        ok.append(f"d'={dp:.2f} — excellent discriminability; identity separation is strong.")
    elif dp >= 2.0:
        ok.append(f"d'={dp:.2f} — good discriminability; pipeline working correctly.")
    elif dp >= 1.0:
        warn.append(f"d'={dp:.2f} — moderate discriminability. Scores have meaningful separation "
                    f"but the distributions partially overlap.")
    else:
        fix.append(f"d'={dp:.2f} — poor discriminability. Genuine and impostor distributions "
                   f"overlap heavily. Check image quality and alignment.")

    # ── TAR / FAR / FRR ──────────────────────────────────────────────────────
    if tar >= 0.90 and far == 0.0 and frr == 0.0:
        ok.append(f"TAR={tar*100:.0f}%, FAR=0%, FRR=0% — current thresholds work well for this dataset.")
    if far > 0.05:
        fix.append(f"FAR={far*100:.1f}% — impostors are being accepted. Raise MATCH_THRESHOLD above {MATCH_THRESHOLD}.")
    elif far > 0.01:
        warn.append(f"FAR={far*100:.1f}% — some impostors accepted. Monitor closely; "
                    f"consider raising MATCH_THRESHOLD.")
    if frr > 0.10:
        fix.append(f"FRR={frr*100:.1f}% — legitimate users are rejected. Lower MATCH_THRESHOLD, "
                   f"or improve image quality.")
    if mrr_i > 0.10:
        warn.append(f"MRR-I={mrr_i*100:.1f}% — {mrr_i*100:.0f}% of impostor pairs fall in manual_review zone "
                    f"({REVIEW_LOWER}–{MATCH_THRESHOLD}). Raise REVIEW_LOWER or tighten MATCH_THRESHOLD.")

    # ── Genuine score distribution vs threshold ───────────────────────────────
    if sg["p25"] < MATCH_THRESHOLD:
        warn.append(
            f"Genuine score p25={sg['p25']:.3f} < MATCH_THRESHOLD={MATCH_THRESHOLD}. "
            f"25% of genuine pairs score below the verified cutoff — they land in manual_review."
        )
    if sg["min"] < REVIEW_LOWER:
        fix.append(
            f"Genuine score min={sg['min']:.3f} < REVIEW_LOWER={REVIEW_LOWER}. "
            f"Some genuine pairs are being hard-rejected. Check image quality."
        )

    # ── Impostor score distribution ───────────────────────────────────────────
    if sd["p90"] >= REVIEW_LOWER:
        warn.append(
            f"Impostor score p90={sd['p90']:.3f} ≥ REVIEW_LOWER={REVIEW_LOWER}. "
            f"Top 10% of impostor pairs enter the manual_review zone. "
            f"Consider raising REVIEW_LOWER to reduce admin burden."
        )
    if sd["max"] >= MATCH_THRESHOLD:
        fix.append(
            f"Impostor score max={sd['max']:.3f} ≥ MATCH_THRESHOLD={MATCH_THRESHOLD}. "
            f"At least one impostor pair was classified as verified — critical threshold issue."
        )

    # ── Dataset size ─────────────────────────────────────────────────────────
    if ng < 10:
        warn.append(f"Only {ng} genuine pairs. Collect ≥3 images per person for robust statistics.")
    if ni < 20:
        warn.append(f"Only {ni} impostor pairs. Add more identities "
                    f"(recommended: ≥10 identities × ≥3 images = ≥270 impostor pairs).")
    if n_identities < 5:
        warn.append(
            f"Only {n_identities} identities tested. FAR estimate is not reliable with < 5 people. "
            f"Expand the test set before drawing conclusions about real-world performance."
        )
    if any(n < 3 for n in n_images_per_identity):
        warn.append(
            "Some identities have < 3 images. Use at least 3 distinct captures "
            "(different session, lighting, angle) per person."
        )

    # ── Threshold tuning guidance ─────────────────────────────────────────────
    margin = sg["mean"] - sd["mean"]
    if dp >= 1.5:
        mid = (sg["mean"] + sd["mean"]) / 2.0
        warn.append(
            f"Suggested threshold exploration: mid-point between genuine/impostor means = {mid:.3f}. "
            f"Compare with EER threshold (see Section 4) for a data-driven starting point."
        )

    print()
    for msg in ok:
        print(f"  ✅  {msg}")
    for msg in warn:
        print(f"  ⚠️   {msg}")
    for msg in fix:
        print(f"  ❌  {msg}")

    print(f"\n  Suggested next steps:")
    print(f"  [1] Expand dataset: ≥10 identities × ≥3 images from different sessions.")
    print(f"  [2] Tune thresholds using EER point (Section 4) as a data-driven starting point.")
    print(f"  [3] Run demographic subgroup analysis (age, gender, skin tone) for bias assessment.")
    print(f"  [4] Re-run benchmark after each hardware/model change to track regressions.")
    print(f"  [5] Document threshold choices + dataset statistics in DPIA supplementary annex.")
    print(f"  [6] NEVER use these R&D results to justify production deployment without DPIA sign-off.")


# ── CLI + entry point ──────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(
            "Biometric Accuracy Benchmark — R&D evaluation. "
            "Output is console-only; scores are never written to logs or API."
        )
    )
    p.add_argument(
        "--identity", action="append", nargs="+", metavar="JPEG", required=True,
        help="JPEG images of one person (repeat flag once per person; ≥2 images per person).",
    )
    p.add_argument("--model", metavar="ONNX_PATH",
                   help="Absolute path to adaface_ir50_webface4m.onnx")
    p.add_argument("--sha256", metavar="HEX",
                   help="Expected SHA-256 hex digest of the model file (optional)")
    p.add_argument("--detector", metavar="DETECTOR_PATH",
                   help="Absolute path to det_500m.onnx (SCRFD-500M face detector)")
    p.add_argument("--detector-sha256", metavar="HEX",
                   help="Expected SHA-256 hex digest of the detector (optional)")
    p.add_argument("--no-per-pair", action="store_true",
                   help="Skip the per-pair listing (useful with large image sets)")
    return p.parse_args()


def main() -> None:
    print("=" * W)
    print("  Biometric Accuracy Benchmark — R&D Only")
    print("  Scores: console only — not in API, UI, or any log.")
    print("=" * W)
    print("  ⚠️  BIOMETRIC_ONNX_RND_ENABLED=true — R&D flag; FORBIDDEN in production.")
    print("  ⚠️  DPIA, DPO sign-off, and bias/fairness audit required before production.")
    print()

    args = _parse_args()
    identities_paths: list[list[str]] = args.identity  # [[path, ...], ...]

    # ── Validate inputs ───────────────────────────────────────────────────────
    if len(identities_paths) < 2:
        print("  ERROR: Need ≥2 --identity groups (one per person).")
        sys.exit(1)
    for i, paths in enumerate(identities_paths):
        if len(paths) < 2:
            print(f"  ERROR: identity[{i}] has {len(paths)} image(s). Need ≥2 per identity.")
            sys.exit(1)

    # ── Load images ───────────────────────────────────────────────────────────
    print(f"  Loading {len(identities_paths)} identities...")
    identities:       list[list[bytes]] = []
    identity_labels:  list[str]         = []
    n_images_per_id:  list[int]         = []

    for i, paths in enumerate(identities_paths):
        images: list[bytes] = []
        label = f"person_{i + 1}"
        for path_str in paths:
            path = Path(path_str)
            if not path.is_file():
                print(f"  ERROR: Image not found: {path_str}")
                sys.exit(1)
            images.append(path.read_bytes())
        identities.append(images)
        identity_labels.append(label)
        n_images_per_id.append(len(images))
        print(f"    {label}:  {len(images)} image(s)  [{', '.join(Path(p).name for p in paths)}]")

    n_genuine_max = sum(n * (n - 1) // 2 for n in n_images_per_id)
    n_impostor_max = sum(
        n_images_per_id[i] * n_images_per_id[j]
        for i in range(len(identities))
        for j in range(i + 1, len(identities))
    )
    print(f"\n  Max genuine pairs:   {n_genuine_max}")
    print(f"  Max impostor pairs:  {n_impostor_max}")

    # ── Load provider ─────────────────────────────────────────────────────────
    model_path    = args.model
    detector_path = getattr(args, "detector", None)

    if not model_path or not Path(model_path).is_file():
        print(f"\n  ERROR: Model file not found: {model_path or '(--model not set)'}")
        print("  Pass --model /absolute/path/to/adaface_ir50_webface4m.onnx")
        sys.exit(1)

    det_active = bool(detector_path and Path(detector_path).is_file())
    print(f"\n  Model:    {Path(model_path).name}")
    print(f"  Detector: {Path(detector_path).name if det_active else '(not set — naive resize; accuracy lower)'}")
    if detector_path and not det_active:
        print(f"  WARNING:  Detector path not found: {detector_path}")

    try:
        import importlib
        import app.config as _cfg
        importlib.reload(_cfg)

        alignment = None
        if det_active:
            import app.services.biometric.face_alignment as _fa
            importlib.reload(_fa)
            from app.services.biometric.face_alignment import FaceAlignmentPipeline
            alignment = FaceAlignmentPipeline()
            print("  ✅  Detector (SCRFD-500M) loaded")

        import app.services.biometric.onnx_provider as _onnx
        importlib.reload(_onnx)
        from app.services.biometric.onnx_provider import OnnxEmbeddingProvider
        provider = OnnxEmbeddingProvider(alignment_pipeline=alignment)
        print("  ✅  Embedding model (AdaFace IR-50) loaded")

    except Exception as exc:
        print(f"\n  ERROR loading models: {exc}")
        sys.exit(1)

    # ── Score collection ──────────────────────────────────────────────────────
    pairs = collect_scores(identities, provider)

    genuine_scores  = [p.score for p in pairs if     p.is_genuine]
    impostor_scores = [p.score for p in pairs if not p.is_genuine]

    print(f"\n  Scored {len(genuine_scores)} genuine pairs, {len(impostor_scores)} impostor pairs")

    if not genuine_scores:
        print("  ERROR: No genuine pairs could be scored — check image quality / face detection.")
        sys.exit(1)
    if not impostor_scores:
        print("  ERROR: No impostor pairs could be scored — need ≥2 distinct identities with valid faces.")
        sys.exit(1)

    # ── Report ────────────────────────────────────────────────────────────────
    report_score_distributions(genuine_scores, impostor_scores)
    counts = report_confusion_matrix(pairs)
    report_outcome_rates(counts)
    report_threshold_sensitivity(genuine_scores, impostor_scores)
    if not args.no_per_pair:
        report_per_pair_details(pairs, identity_labels)
    report_recommendations(
        genuine_scores, impostor_scores, counts,
        n_identities=len(identities),
        n_images_per_identity=n_images_per_id,
    )

    # ── Footer ────────────────────────────────────────────────────────────────
    n_imgs = sum(len(imgs) for imgs in identities)
    print(f"\n{'═' * W}")
    print(f"  BENCHMARK COMPLETE")
    print(
        f"  {len(identities)} identities  │  {n_imgs} images  │"
        f"  {len(genuine_scores)} genuine pairs  │  {len(impostor_scores)} impostor pairs"
    )
    det_label = "SCRFD-500M + AdaFace IR-50" if det_active else "AdaFace IR-50 (naive resize, no detector)"
    print(f"  Pipeline:  {det_label}")
    print(f"  Thresholds: REVIEW_LOWER={REVIEW_LOWER}  MATCH_THRESHOLD={MATCH_THRESHOLD}")
    print(f"{'═' * W}")
    print()
    print("  REMINDER: R&D only. Console output only. Not for production.")
    print("  DPIA and DPO approval required before any production use.")
    print()


if __name__ == "__main__":
    main()
