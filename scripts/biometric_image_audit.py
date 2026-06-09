"""
Biometric Image Audit — per-image quality + per-pair score analysis.

Extracts SCRFD detection metrics (face size, head pose, confidence) for every
test image, computes all pairwise cosine similarity scores, and produces a
comprehensive audit report:

  1. Per-image quality metrics
  2. All genuine pairs with scores (sorted)
  3. All impostor pairs with scores (sorted)
  4. Threshold impact at 0.75 / 0.65 / 0.63
  5. Root-cause analysis for manual_review pairs
  6. Retake / keep recommendations per image

Usage:
  python scripts/biometric_image_audit.py \
    --identity person_1 scripts/test_images/person_1/*.jpg \
    --identity person_2 scripts/test_images/person_2/*.jpg \
    ...
    --model    local_models/adaface_ir50_webface4m.onnx \
    --detector local_models/det_500m.onnx

R&D only — scores console-only, images not committed.
"""
from __future__ import annotations

import argparse
import math
import os
import pathlib
import sys
from dataclasses import dataclass, field
from typing import Optional

# ── Python path ────────────────────────────────────────────────────────────────
_ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# ── R&D env ────────────────────────────────────────────────────────────────────
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

# ── Thresholds ─────────────────────────────────────────────────────────────────
THRESHOLDS = {
    "T0.75 (current)":  0.75,
    "T0.65 (R&D rec.)": 0.65,
    "T0.63 (optimal)":  0.63,
}
REVIEW_LOWER = 0.55

W = 72


# ── Data containers ────────────────────────────────────────────────────────────

@dataclass
class FaceMetrics:
    """Per-image SCRFD detection results."""
    img_label:       str
    person_label:    str
    detected:        bool
    confidence:      float     = 0.0
    face_w_px:       float     = 0.0
    face_h_px:       float     = 0.0
    img_w_px:        int       = 0
    img_h_px:        int       = 0
    face_area_pct:   float     = 0.0   # face bbox area / image area × 100
    center_offset:   float     = 0.0   # Euclidean distance of face center from image center (normalized)
    yaw_norm:        float     = 0.0   # signed normalized yaw estimate from landmarks
    roll_deg:        float     = 0.0   # roll angle (degrees)
    pitch_norm:      float     = 0.0   # signed normalized pitch estimate
    eye_distance_px: float     = 0.0   # inter-ocular distance in pixels
    embedding:       list[float] = field(default_factory=list)
    error:           str       = ""

    # ── Derived quality flags ──────────────────────────────────────────────────

    @property
    def frontality(self) -> str:
        if not self.detected:
            return "no_face"
        if abs(self.yaw_norm) < 0.15:
            return "frontal"
        if abs(self.yaw_norm) < 0.30:
            return "slight_turn"
        return "significant_turn"

    @property
    def face_size_quality(self) -> str:
        if not self.detected:
            return "no_face"
        if self.face_area_pct >= 20:
            return "large"
        if self.face_area_pct >= 10:
            return "good"
        if self.face_area_pct >= 5:
            return "small"
        return "too_small"

    @property
    def confidence_quality(self) -> str:
        if not self.detected:
            return "no_face"
        if self.confidence >= 0.95:
            return "excellent"
        if self.confidence >= 0.85:
            return "good"
        if self.confidence >= 0.70:
            return "fair"
        return "low"

    @property
    def overall_quality(self) -> str:
        """Aggregate quality: good / fair / poor."""
        if not self.detected:
            return "FAIL"
        flags = [self.frontality, self.face_size_quality, self.confidence_quality]
        if "significant_turn" in flags or "too_small" in flags or "low" in flags:
            return "poor"
        if "slight_turn" in flags or "small" in flags or "fair" in flags:
            return "fair"
        return "good"


@dataclass
class PairResult:
    id_i:      str
    img_i:     str
    id_j:      str
    img_j:     str
    score:     float
    is_genuine: bool

    def outcome(self, threshold: float = 0.75, review_lower: float = REVIEW_LOWER) -> str:
        if self.score >= threshold:
            return "verified"
        if self.score >= review_lower:
            return "manual_review"
        return "rejected"


# ── SCRFD + embedding extraction ───────────────────────────────────────────────

def _cosine(a: list[float], b: list[float]) -> float:
    dot  = sum(x * y for x, y in zip(a, b))
    na   = math.sqrt(sum(x * x for x in a))
    nb   = math.sqrt(sum(x * x for x in b))
    return dot / (na * nb) if (na > 0 and nb > 0) else 0.0


def extract_face_metrics(
    img_bytes: bytes,
    img_label: str,
    person_label: str,
    pipeline,   # FaceAlignmentPipeline | None
    provider,   # OnnxEmbeddingProvider
) -> FaceMetrics:
    """Run SCRFD + AdaFace on one image; return FaceMetrics with embedding."""
    from PIL import Image
    import io, numpy as np

    m = FaceMetrics(img_label=img_label, person_label=person_label, detected=False)

    # Image dimensions
    try:
        pil = Image.open(io.BytesIO(img_bytes)).convert("RGB")
        m.img_w_px, m.img_h_px = pil.size
    except Exception as e:
        m.error = f"decode_failed: {e}"
        return m

    if pipeline is None:
        # No detector — just embed and skip face metrics
        try:
            m.embedding = provider.generate(img_bytes)
            m.detected  = True
            m.confidence = 1.0
            m.face_area_pct = 100.0
        except Exception as e:
            m.error = str(e)[:80]
        return m

    # SCRFD detection
    try:
        boxes, scores, kps_all = pipeline._detect_faces(pil)
    except Exception as e:
        m.error = f"detect_failed: {e}"
        return m

    if boxes.shape[0] == 0:
        m.error = "no_face_detected"
        return m

    # Best face (highest confidence)
    best    = int(scores.argmax())
    box     = boxes[best]   # x1, y1, x2, y2
    conf    = float(scores[best])
    kps     = kps_all[best] # (5, 2)

    m.detected    = True
    m.confidence  = conf
    m.face_w_px   = float(box[2] - box[0])
    m.face_h_px   = float(box[3] - box[1])
    m.face_area_pct = (m.face_w_px * m.face_h_px) / (m.img_w_px * m.img_h_px) * 100

    # Face center offset from image center (normalized 0-1)
    cx = (box[0] + box[2]) / 2 / m.img_w_px - 0.5
    cy = (box[1] + box[3]) / 2 / m.img_h_px - 0.5
    m.center_offset = math.sqrt(cx * cx + cy * cy)

    # ── Head pose from 5 landmarks ─────────────────────────────────────────────
    # kps order: left_eye, right_eye, nose_tip, mouth_left, mouth_right
    le, re, nt = kps[0], kps[1], kps[2]

    eye_mid_x = (le[0] + re[0]) / 2
    eye_mid_y = (le[1] + re[1]) / 2
    eye_dist  = math.sqrt((re[0] - le[0]) ** 2 + (re[1] - le[1]) ** 2)
    m.eye_distance_px = eye_dist

    # Yaw: nose_x vs eye_midpoint_x, normalized by eye_distance
    # Positive = nose shifted right = face turned right (from viewer perspective)
    m.yaw_norm  = (nt[0] - eye_mid_x) / eye_dist if eye_dist > 0 else 0.0
    # Pitch: nose_y below eye_midpoint, normalized; negative = looking up
    m.pitch_norm = (nt[1] - eye_mid_y) / eye_dist if eye_dist > 0 else 0.0
    # Roll: angle of eye line
    m.roll_deg  = math.degrees(math.atan2(
        float(re[1] - le[1]), float(re[0] - le[0])
    ))

    # ── Embedding ──────────────────────────────────────────────────────────────
    try:
        m.embedding = provider.generate(img_bytes)
    except Exception as e:
        m.error = f"embed_failed: {e}"

    return m


# ── Reporting helpers ──────────────────────────────────────────────────────────

def _section(title: str) -> None:
    print(f"\n── {title} {'─' * max(0, W - 4 - len(title))}")


def _quality_icon(q: str) -> str:
    return {"good": "✅", "fair": "⚠️ ", "poor": "❌", "FAIL": "💀"}.get(q, "  ")


def _front_icon(f: str) -> str:
    return {"frontal": "✅", "slight_turn": "⚠️ ", "significant_turn": "❌", "no_face": "💀"}.get(f, "  ")


def _outcome_icon(o: str) -> str:
    return {"verified": "✅", "manual_review": "⚠️ ", "rejected": "❌"}.get(o, "")


# ── Main report ────────────────────────────────────────────────────────────────

def run_audit(
    identities: dict[str, list[tuple[str, bytes]]],   # label → [(img_label, bytes)]
    pipeline,
    provider,
) -> None:

    # ── 1. Extract per-image metrics ───────────────────────────────────────────
    _section("PER-IMAGE QUALITY METRICS  (SCRFD detection)")

    all_metrics: dict[str, FaceMetrics] = {}   # "person_N/img_x" → FaceMetrics
    for p_label, images in identities.items():
        print(f"\n  {p_label}:")
        print(f"  {'Image':<8s}  {'Q':2s}  {'Front':<18s}  {'Conf':>6s}  "
              f"{'Face%':>6s}  {'EyeDist':>7s}  {'Yaw':>7s}  {'Roll':>7s}  {'Note'}")
        print(f"  {'─'*8}  {'─'*2}  {'─'*18}  {'─'*6}  {'─'*6}  {'─'*7}  {'─'*7}  {'─'*7}  {'─'*20}")
        for img_label, img_bytes in images:
            key = f"{p_label}/{img_label}"
            m   = extract_face_metrics(img_bytes, img_label, p_label, pipeline, provider)
            all_metrics[key] = m
            note = m.error if m.error else ""
            if not m.detected:
                print(f"  {img_label:<8s}  💀   {'no_face':<18s}  {'—':>6s}  "
                      f"{'—':>6s}  {'—':>7s}  {'—':>7s}  {'—':>7s}  {note}")
            else:
                print(f"  {img_label:<8s}  "
                      f"{_quality_icon(m.overall_quality)}  "
                      f"{_front_icon(m.frontality)} {m.frontality:<14s}  "
                      f"{m.confidence:>6.3f}  "
                      f"{m.face_area_pct:>5.1f}%  "
                      f"{m.eye_distance_px:>7.1f}  "
                      f"{m.yaw_norm:>+7.3f}  "
                      f"{m.roll_deg:>+6.1f}°  "
                      f"{note}")

    # ── 2. Compute all pairwise scores ─────────────────────────────────────────
    person_labels = list(identities.keys())
    pairs: list[PairResult] = []

    for i, pi in enumerate(person_labels):
        imgs_i = [(f"{pi}/{lbl}", lbl, bytes_) for lbl, bytes_ in identities[pi]]
        for j, pj in enumerate(person_labels):
            if j < i:
                continue
            is_genuine = (i == j)
            imgs_j = [(f"{pj}/{lbl}", lbl, bytes_) for lbl, bytes_ in identities[pj]]
            for ki, (key_i, lbl_i, _) in enumerate(imgs_i):
                start = ki + 1 if is_genuine else 0
                for kj in range(start, len(imgs_j)):
                    key_j, lbl_j, _ = imgs_j[kj]
                    mi = all_metrics.get(key_i)
                    mj = all_metrics.get(key_j)
                    if not mi or not mj or not mi.embedding or not mj.embedding:
                        continue
                    score = _cosine(mi.embedding, mj.embedding)
                    pairs.append(PairResult(pi, lbl_i, pj, lbl_j, score, is_genuine))

    genuine_pairs  = sorted([p for p in pairs if     p.is_genuine], key=lambda x: -x.score)
    impostor_pairs = sorted([p for p in pairs if not p.is_genuine], key=lambda x: -x.score)

    # ── 3. Genuine pairs — full listing ───────────────────────────────────────
    _section("GENUINE PAIRS — full listing  (sorted by score desc)")
    print(f"\n  {'Person':<10s}  {'Img A':<7s}  {'Img B':<7s}  {'Score':>7s}  "
          f"{'T=0.75':<14s}  {'T=0.65':<14s}  {'T=0.63':<14s}")
    print(f"  {'─'*10}  {'─'*7}  {'─'*7}  {'─'*7}  {'─'*14}  {'─'*14}  {'─'*14}")
    for p in genuine_pairs:
        o75 = p.outcome(0.75)
        o65 = p.outcome(0.65)
        o63 = p.outcome(0.63)
        print(f"  {p.id_i:<10s}  {p.img_i:<7s}  {p.img_j:<7s}  {p.score:>7.4f}  "
              f"{_outcome_icon(o75)} {o75:<12s}  "
              f"{_outcome_icon(o65)} {o65:<12s}  "
              f"{_outcome_icon(o63)} {o63:<12s}")

    # ── 4. Impostor pairs — top-20 highest (most dangerous) ───────────────────
    _section("IMPOSTOR PAIRS — ranked by score  (highest = most dangerous)")
    print(f"\n  {'Person A':<10s}  {'Img':<7s}  {'Person B':<10s}  {'Img':<7s}  {'Score':>7s}  {'Outcome'}")
    print(f"  {'─'*10}  {'─'*7}  {'─'*10}  {'─'*7}  {'─'*7}  {'─'*10}")
    for p in impostor_pairs[:20]:
        o = p.outcome(0.75)
        icon = _outcome_icon(o)
        print(f"  {p.id_i:<10s}  {p.img_i:<7s}  {p.id_j:<10s}  {p.img_j:<7s}  "
              f"{p.score:>7.4f}  {icon} {o}")
    if len(impostor_pairs) > 20:
        remaining = impostor_pairs[20:]
        all_rejected = all(p.outcome(0.75) == "rejected" for p in remaining)
        print(f"\n  ... {len(remaining)} more pairs, all rejected "
              f"(max score in remaining: {remaining[0].score:.4f})"
              if all_rejected else
              f"\n  ... {len(remaining)} more pairs")

    # ── 5. Threshold impact matrix ─────────────────────────────────────────────
    _section("THRESHOLD IMPACT MATRIX")
    for t_label, t_val in THRESHOLDS.items():
        g_ver  = sum(1 for p in genuine_pairs  if p.outcome(t_val) == "verified")
        g_rev  = sum(1 for p in genuine_pairs  if p.outcome(t_val) == "manual_review")
        g_rej  = sum(1 for p in genuine_pairs  if p.outcome(t_val) == "rejected")
        i_ver  = sum(1 for p in impostor_pairs if p.outcome(t_val) == "verified")
        i_rev  = sum(1 for p in impostor_pairs if p.outcome(t_val) == "manual_review")
        i_rej  = sum(1 for p in impostor_pairs if p.outcome(t_val) == "rejected")
        ng, ni = len(genuine_pairs), len(impostor_pairs)
        print(f"\n  {t_label}")
        print(f"    Genuine  ({ng:2d}):  "
              f"verified={g_ver:2d} ({100*g_ver//ng:3d}%)  "
              f"manual_review={g_rev:2d} ({100*g_rev//ng:3d}%)  "
              f"rejected={g_rej:2d} ({100*g_rej//ng:3d}%)")
        print(f"    Impostor ({ni:2d}):  "
              f"verified={i_ver:2d} ({100*i_ver//ni:3d}%)  "
              f"manual_review={i_rev:2d} ({100*i_rev//ni:3d}%)  "
              f"rejected={i_rej:2d} ({100*i_rej//ni:3d}%)")
        tar = g_ver / ng; far = i_ver / ni; frr = g_rej / ng
        print(f"    → TAR={tar*100:.1f}%  FAR={far*100:.1f}%  FRR={frr*100:.1f}%")

    # ── 6. Root-cause analysis for manual_review genuine pairs ─────────────────
    _section("ROOT-CAUSE ANALYSIS — manual_review genuine pairs  (T=0.75)")
    manual_pairs = [p for p in genuine_pairs if p.outcome(0.75) == "manual_review"]
    if not manual_pairs:
        print("  No manual_review pairs at T=0.75.")
    else:
        for p in manual_pairs:
            print(f"\n  {p.id_i}  {p.img_i} ↔ {p.img_j}  score={p.score:.4f}")
            mi = all_metrics.get(f"{p.id_i}/{p.img_i}")
            mj = all_metrics.get(f"{p.id_j}/{p.img_j}")
            causes = []
            # Compare the two images
            if mi and mj and mi.detected and mj.detected:
                yaw_diff  = abs(mi.yaw_norm  - mj.yaw_norm)
                roll_diff = abs(mi.roll_deg  - mj.roll_deg)
                size_diff = abs(mi.face_area_pct - mj.face_area_pct)
                conf_min  = min(mi.confidence, mj.confidence)

                print(f"    {p.img_i}: yaw={mi.yaw_norm:+.3f}  roll={mi.roll_deg:+.1f}°  "
                      f"face={mi.face_area_pct:.1f}%  conf={mi.confidence:.3f}  "
                      f"quality={mi.overall_quality}")
                print(f"    {p.img_j}: yaw={mj.yaw_norm:+.3f}  roll={mj.roll_deg:+.1f}°  "
                      f"face={mj.face_area_pct:.1f}%  conf={mj.confidence:.3f}  "
                      f"quality={mj.overall_quality}")

                if yaw_diff > 0.20:
                    causes.append(f"yaw_mismatch ({yaw_diff:.2f} normalized units)")
                if roll_diff > 10:
                    causes.append(f"roll_mismatch ({roll_diff:.1f}°)")
                if size_diff > 15:
                    causes.append(f"scale_difference ({size_diff:.1f}% face area)")
                if conf_min < 0.85:
                    causes.append(f"low_detection_confidence ({conf_min:.3f})")
                if mi.overall_quality == "poor" or mj.overall_quality == "poor":
                    weak = p.img_i if mi.overall_quality == "poor" else p.img_j
                    causes.append(f"poor_image_quality ({weak})")
                if not causes:
                    causes.append("moderate_intra-person_variation (within acceptable range)")
            else:
                if not mi or not mi.detected:
                    causes.append(f"face_not_detected in {p.img_i}")
                if not mj or not mj.detected:
                    causes.append(f"face_not_detected in {p.img_j}")

            print(f"    Root causes: {'; '.join(causes) if causes else 'none identified'}")
            # Score distance from threshold
            gap_to_verified = 0.75 - p.score
            gap_to_t65 = 0.65 - p.score
            print(f"    Gap to T=0.75: {gap_to_verified:+.4f}  |  "
                  f"Gap to T=0.65: {gap_to_t65:+.4f}"
                  f"{'  → verified at T=0.65' if gap_to_t65 <= 0 else ''}")

    # ── 7. Per-identity average score analysis ─────────────────────────────────
    _section("PER-IDENTITY GENUINE SCORE ANALYSIS")
    for p_label in person_labels:
        id_pairs = [p for p in genuine_pairs if p.id_i == p_label]
        if not id_pairs:
            continue
        scores = [p.score for p in id_pairs]
        avg    = sum(scores) / len(scores)
        mn, mx = min(scores), max(scores)
        # Find which image is the weak anchor
        img_scores: dict[str, list[float]] = {}
        for p in id_pairs:
            img_scores.setdefault(p.img_i, []).append(p.score)
            img_scores.setdefault(p.img_j, []).append(p.score)
        img_avgs = {img: sum(v) / len(v) for img, v in img_scores.items()}
        weakest  = min(img_avgs, key=img_avgs.get)
        strongest = max(img_avgs, key=img_avgs.get)
        print(f"\n  {p_label}:  avg={avg:.4f}  min={mn:.4f}  max={mx:.4f}  "
              f"strongest_anchor={strongest} ({img_avgs[strongest]:.4f})  "
              f"weakest_anchor={weakest} ({img_avgs[weakest]:.4f})")
        for img, avg_s in sorted(img_avgs.items(), key=lambda x: -x[1]):
            m = all_metrics.get(f"{p_label}/{img}")
            q = m.overall_quality if m else "?"
            print(f"    {img}:  avg_score={avg_s:.4f}  quality={q}  "
                  f"{'⚠️  weakest anchor' if img == weakest else ''}")

    # ── 8. Recommendations per image ───────────────────────────────────────────
    _section("IMAGE RECOMMENDATIONS")
    print(f"\n  {'Person':<10s}  {'Image':<7s}  {'Quality':<6s}  "
          f"{'Ref?':<10s}  {'Verify?':<10s}  {'Action'}")
    print(f"  {'─'*10}  {'─'*7}  {'─'*6}  {'─'*10}  {'─'*10}  {'─'*30}")

    for p_label in person_labels:
        id_pairs = [p for p in genuine_pairs if p.id_i == p_label]
        img_scores: dict[str, list[float]] = {}
        for p in id_pairs:
            img_scores.setdefault(p.img_i, []).append(p.score)
            img_scores.setdefault(p.img_j, []).append(p.score)
        img_avgs = {img: sum(v) / len(v) for img, v in img_scores.items()}

        for lbl, _ in identities[p_label]:
            key = f"{p_label}/{lbl}"
            m   = all_metrics.get(key)
            avg_s = img_avgs.get(lbl, 0.0)
            q    = m.overall_quality if m else "?"

            # Reference suitability: high confidence, frontal, good size
            ref_ok = (m and m.detected and m.confidence >= 0.85
                      and abs(m.yaw_norm) < 0.25 and m.face_area_pct >= 8)
            # Verify suitability: similar to reference criteria
            ver_ok = ref_ok and avg_s >= 0.65

            action = ""
            if not m or not m.detected:
                action = "RETAKE — no face detected"
            elif q == "poor":
                action = "RETAKE — poor quality"
            elif avg_s < 0.65 and q == "fair":
                action = "RETAKE recommended — weak avg score"
            elif avg_s < 0.70:
                action = "Consider retake (score marginal)"
            else:
                action = "Keep"

            ref_str = "✅ suitable" if ref_ok else "⚠️  marginal"
            ver_str = "✅ suitable" if ver_ok else "⚠️  marginal"
            print(f"  {p_label:<10s}  {lbl:<7s}  {q:<6s}  "
                  f"{ref_str:<10s}  {ver_str:<10s}  {action}")

    # ── 9. Overall summary ─────────────────────────────────────────────────────
    print(f"\n{'═' * W}")
    print(f"  AUDIT COMPLETE")
    ng = len(genuine_pairs); ni = len(impostor_pairs)
    print(f"  {len(person_labels)} identities  │  "
          f"{sum(len(v) for v in identities.values())} images  │  "
          f"{ng} genuine pairs  │  {ni} impostor pairs")
    # Quick stats at T=0.75 and T=0.65
    for t_label, t_val in THRESHOLDS.items():
        tar = sum(1 for p in genuine_pairs  if p.outcome(t_val)=="verified") / ng
        far = sum(1 for p in impostor_pairs if p.outcome(t_val)=="verified") / ni
        frr = sum(1 for p in genuine_pairs  if p.outcome(t_val)=="rejected") / ng
        print(f"  {t_label:<20s}  TAR={tar*100:.0f}%  FAR={far*100:.0f}%  FRR={frr*100:.0f}%")
    print(f"{'═' * W}")
    print("  R&D only. Console output only. DPIA required before production.")


# ── CLI ────────────────────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Biometric image audit — per-image quality + per-pair scores")
    p.add_argument("--identity", action="append", nargs="+", metavar="ARG",
                   help="First arg = person label, remainder = JPEG paths")
    p.add_argument("--model",            metavar="ONNX")
    p.add_argument("--sha256",           metavar="HEX")
    p.add_argument("--detector",         metavar="ONNX")
    p.add_argument("--detector-sha256",  metavar="HEX")
    return p.parse_args()


def main() -> None:
    print("=" * W)
    print("  Biometric Image Audit — R&D Only")
    print("=" * W)
    print("  ⚠️  Scores console-only. Images not committed. R&D flag active.")

    args = _parse_args()

    # ── Load images ────────────────────────────────────────────────────────────
    identities: dict[str, list[tuple[str, bytes]]] = {}
    for group in (args.identity or []):
        if len(group) < 2:
            print(f"  ERROR: --identity needs label + ≥1 image path")
            sys.exit(1)
        p_label = group[0]
        images  = []
        for path_str in group[1:]:
            path = pathlib.Path(path_str)
            if not path.is_file():
                print(f"  ERROR: not found: {path_str}")
                sys.exit(1)
            images.append((path.name.replace(".jpg", "").replace(".jpeg", ""), path.read_bytes()))
        identities[p_label] = images
        print(f"  Loaded {p_label}: {[lbl for lbl,_ in images]}")

    if len(identities) < 2:
        print("  ERROR: need ≥2 --identity groups")
        sys.exit(1)

    # ── Load models ────────────────────────────────────────────────────────────
    model_path    = args.model
    detector_path = getattr(args, "detector", None)

    if not model_path or not pathlib.Path(model_path).is_file():
        print(f"  ERROR: model not found: {model_path}")
        sys.exit(1)

    import importlib
    import app.config as _cfg; importlib.reload(_cfg)

    pipeline = None
    if detector_path and pathlib.Path(detector_path).is_file():
        import app.services.biometric.face_alignment as _fa; importlib.reload(_fa)
        from app.services.biometric.face_alignment import FaceAlignmentPipeline
        pipeline = FaceAlignmentPipeline()
        print("  ✅  SCRFD detector loaded")
    else:
        print("  ⚠️  No detector — naive resize mode")

    import app.services.biometric.onnx_provider as _onnx; importlib.reload(_onnx)
    from app.services.biometric.onnx_provider import OnnxEmbeddingProvider
    provider = OnnxEmbeddingProvider(alignment_pipeline=pipeline)
    print("  ✅  AdaFace IR-50 loaded")

    run_audit(identities, pipeline, provider)


if __name__ == "__main__":
    main()
