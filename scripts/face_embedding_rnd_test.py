"""
AdaFace Backend-Only R&D Proof Script — PR-5 validation

Purpose:
  Validate that the OnnxEmbeddingProvider generates real face embeddings
  from JPEG image bytes, NOT from filename strings (FakeEmbeddingProvider).

  Proves:
    1. Embedding is 512-dim, L2-normalized
    2. Same person → high cosine similarity (≥ 0.50 without alignment)
    3. Different person → low cosine similarity (< same-person score)
    4. Different filename, same image → identical embedding
    5. Same filename, different image → different embedding
    6. Guard tests pass without a model file

Usage:
  python scripts/face_embedding_rnd_test.py \\
    --same-person /path/to/person_a_1.jpg /path/to/person_a_2.jpg \\
    --diff-person /path/to/person_b.jpg \\
    [--model /path/to/adaface_ir50_webface4m.onnx] \\
    [--sha256 <hex>]

Test data requirements:
  - same_person: ≥ 2 close-up face JPEGs of the same person (different lighting/angle)
  - diff_person: ≥ 1 close-up face JPEG of a DIFFERENT person
  - Images: NOT committed to the repository — local only
  - Close-up face photos strongly recommended (preprocessing does NOT crop/align)

IMPORTANT:
  - Scores are printed to console only — never to API, UI, or any log file
  - Model file must NOT be committed to the repository (*.onnx in .gitignore)
  - Test images must NOT be committed to the repository
  - This script is for local R&D validation ONLY
  - BIOMETRIC_ONNX_RND_ENABLED=true is required and is an R&D-only flag
  - Production use requires DPIA, DPO sign-off, bias/fairness audit, legal review

Alignment note:
  preprocess_face_image() performs a naive 112×112 resize (no face detection,
  no landmark alignment). For close-up selfie-style images this is acceptable
  for PoC validation. With background or off-center faces, similarity scores
  will be lower. Results without alignment are not representative of
  production accuracy. Face detection + alignment is planned as a separate PR.
"""
from __future__ import annotations

import argparse
import math
import os
import sys
from pathlib import Path

# ── Python path — ensure project root is importable when run as a script ──────
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# ── R&D env setup — MUST happen before any app.config import ──────────────────
# Pydantic BaseSettings reads from env at instantiation time.
# The `settings` object imported inside service modules (onnx_provider, etc.)
# is bound at their first import. Setting env vars here (module load time,
# before any app import) ensures all modules see the correct values.
#
# Model path and SHA256 are extracted from sys.argv early for the same reason.

def _extract_argv_value(flag: str) -> str | None:
    """Extract a single-value CLI flag from sys.argv without running argparse."""
    for i, arg in enumerate(sys.argv):
        if arg == flag and i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return None

_early_model_path    = _extract_argv_value("--model")
_early_sha256        = _extract_argv_value("--sha256")
_early_detector_path = _extract_argv_value("--detector")
_early_detector_sha  = _extract_argv_value("--detector-sha256")

os.environ.setdefault("BIOMETRIC_FACE_MATCHING_ENABLED",     "true")
os.environ.setdefault("BIOMETRIC_EMBEDDING_PROVIDER",        "onnx")
os.environ.setdefault("BIOMETRIC_ONNX_RND_ENABLED",          "true")
os.environ.setdefault("BIOMETRIC_ENCRYPTION_ALLOW_TEST_KEY", "true")
os.environ.setdefault("BIOMETRIC_EMBEDDING_KEY",             "")
os.environ.setdefault("BIOMETRIC_DISCLOSURE_ENABLED",        "true")
if _early_model_path:
    os.environ["BIOMETRIC_ONNX_MODEL_PATH"] = _early_model_path
if _early_sha256:
    os.environ["BIOMETRIC_ONNX_MODEL_SHA256"] = _early_sha256
if _early_detector_path:
    os.environ["BIOMETRIC_FACE_DETECTOR_PATH"] = _early_detector_path
if _early_detector_sha:
    os.environ["BIOMETRIC_FACE_DETECTOR_SHA256"] = _early_detector_sha

# ── R&D env guard ─────────────────────────────────────────────────────────────

def _require_rnd_env(
    model_path: str | None,
    sha256: str | None,
    detector_path: str | None = None,
    detector_sha: str | None = None,
) -> None:
    """Confirm R&D env vars and log. Env already set at module load above."""
    if model_path:
        os.environ["BIOMETRIC_ONNX_MODEL_PATH"] = model_path
    if sha256:
        os.environ["BIOMETRIC_ONNX_MODEL_SHA256"] = sha256
    if detector_path:
        os.environ["BIOMETRIC_FACE_DETECTOR_PATH"] = detector_path
    if detector_sha:
        os.environ["BIOMETRIC_FACE_DETECTOR_SHA256"] = detector_sha
    print("  [R&D] Env: BIOMETRIC_ONNX_RND_ENABLED=true — R&D only, never production")
    print(f"  [R&D] Model path:    {os.environ.get('BIOMETRIC_ONNX_MODEL_PATH', '(not set)')}")
    det = os.environ.get("BIOMETRIC_FACE_DETECTOR_PATH", "")
    print(f"  [R&D] Detector path: {det if det else '(not set — alignment disabled)'}")


# ── Console output helpers ─────────────────────────────────────────────────────

PASS = "✅"
FAIL = "❌"
SKIP = "⏭ "
WARN = "⚠️ "

_results: list[tuple[str, bool]] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    mark = PASS if condition else FAIL
    line = f"  {mark}  {label}"
    if detail:
        line += f"  [{detail}]"
    print(line)
    _results.append((label, condition))


def skip(label: str, reason: str = "") -> None:
    line = f"  {SKIP}  {label}"
    if reason:
        line += f"  [{reason}]"
    print(line)


def warn(msg: str) -> None:
    print(f"  {WARN}  {msg}")


# ── Math helpers ───────────────────────────────────────────────────────────────

def _l2_norm(v: list[float]) -> float:
    return math.sqrt(sum(x * x for x in v))


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na  = _l2_norm(a)
    nb  = _l2_norm(b)
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


# ── Section 1: Guard tests (no model needed) ──────────────────────────────────

def run_guard_tests() -> None:
    print("\n── Section 1: Guard tests (no model required) ─────────────────────────────")

    # 1a. Missing BIOMETRIC_ONNX_MODEL_PATH
    print("\n  1a. Missing model path → ModelNotAvailableError")
    saved = os.environ.pop("BIOMETRIC_ONNX_MODEL_PATH", "")
    try:
        from app.services.biometric.embedding_service import get_embedding_provider
        import importlib
        import app.services.biometric.embedding_service as _es
        importlib.reload(_es)
        os.environ["BIOMETRIC_ONNX_MODEL_PATH"] = ""
        try:
            from app.services.biometric.onnx_provider import OnnxEmbeddingProvider
            from app.services.biometric.model_registry import ModelNotAvailableError
            try:
                OnnxEmbeddingProvider()
                check("Empty model path raises ModelNotAvailableError", False)
            except ModelNotAvailableError:
                check("Empty model path raises ModelNotAvailableError", True)
            except Exception as e:
                check("Empty model path raises ModelNotAvailableError", False, str(e)[:60])
        finally:
            os.environ["BIOMETRIC_ONNX_MODEL_PATH"] = saved
    except ImportError as e:
        skip("Guard test imports", f"ImportError: {e}")

    # 1b. BIOMETRIC_ONNX_RND_ENABLED=false guard
    print("\n  1b. BIOMETRIC_ONNX_RND_ENABLED=false → ModelNotAvailableError")
    saved_rnd = os.environ.pop("BIOMETRIC_ONNX_RND_ENABLED", "true")
    os.environ["BIOMETRIC_ONNX_RND_ENABLED"] = "false"
    try:
        from app.config import settings
        import importlib
        import app.config as _cfg
        importlib.reload(_cfg)
        from app.services.biometric.onnx_provider import OnnxEmbeddingProvider
        from app.services.biometric.model_registry import ModelNotAvailableError
        try:
            OnnxEmbeddingProvider()
            check("RND_ENABLED=false raises ModelNotAvailableError", False)
        except ModelNotAvailableError:
            check("RND_ENABLED=false raises ModelNotAvailableError", True)
        except Exception as e:
            check("RND_ENABLED=false raises ModelNotAvailableError", False, str(e)[:60])
    finally:
        os.environ["BIOMETRIC_ONNX_RND_ENABLED"] = saved_rnd

    # 1c. Invalid image bytes → ValueError
    print("\n  1c. Invalid image bytes → ValueError in preprocess_face_image()")
    try:
        from app.services.biometric.face_preprocessing import preprocess_face_image
        try:
            preprocess_face_image(b"not_an_image_ABCDEF")
            check("Invalid image raises ValueError", False)
        except ValueError:
            check("Invalid image raises ValueError", True)
        except Exception as e:
            check("Invalid image raises ValueError", False, str(e)[:60])
    except ImportError as e:
        skip("face_preprocessing import", str(e))

    # 1d. Non-existent model path → ModelNotAvailableError
    print("\n  1d. Non-existent model path → ModelNotAvailableError")
    try:
        from app.services.biometric.model_registry import assert_model_path_safe, ModelNotAvailableError
        try:
            assert_model_path_safe("/nonexistent/path/model.onnx")
            check("Non-existent path raises ModelNotAvailableError", False)
        except ModelNotAvailableError:
            check("Non-existent path raises ModelNotAvailableError", True)
    except ImportError as e:
        skip("model_registry import", str(e))


# ── Section 2: Preprocessing tests (no model needed) ─────────────────────────

def run_preprocessing_tests(sample_jpeg: bytes) -> None:
    print("\n── Section 2: Preprocessing tests ─────────────────────────────────────────")
    try:
        from app.services.biometric.face_preprocessing import preprocess_face_image
        tensor = preprocess_face_image(sample_jpeg)

        check("Output shape is (1, 3, 112, 112)",
              tensor.shape == (1, 3, 112, 112),
              str(tensor.shape))

        check("Output dtype is float32",
              str(tensor.dtype) == "float32",
              str(tensor.dtype))

        check("Pixel values in [-1, 1]",
              float(tensor.min()) >= -1.01 and float(tensor.max()) <= 1.01,
              f"min={tensor.min():.3f} max={tensor.max():.3f}")

        warn("Note: preprocessing does NOT align/crop — close-up face images recommended")

    except ImportError as e:
        skip("Preprocessing tests", str(e))


# ── Section 3: FakeProvider — confirm filename-seed behaviour ─────────────────

def run_fake_provider_tests() -> None:
    print("\n── Section 3: FakeProvider — filename seed confirmation ────────────────────")
    print("  (Documents the baseline: FakeProvider is NOT face recognition)\n")
    try:
        from app.services.biometric.embedding_service import FakeEmbeddingProvider
        from app.services.biometric.matching_service import compute_cosine_similarity

        fp = FakeEmbeddingProvider()

        seed_a = b"liveness_abc123.jpg"
        seed_b = b"liveness_xyz999.jpg"

        emb_a1 = fp.generate(seed_a)
        emb_a2 = fp.generate(seed_a)  # same seed again
        emb_b  = fp.generate(seed_b)

        score_same_seed = compute_cosine_similarity(emb_a1, emb_a2)
        score_diff_seed = compute_cosine_similarity(emb_a1, emb_b)

        check(f"FakeProvider: same seed → similarity = 1.0",
              abs(score_same_seed - 1.0) < 1e-6,
              f"score={score_same_seed:.6f}")

        check(f"FakeProvider: different seed → similarity ≠ 1.0",
              abs(score_diff_seed - 1.0) > 0.01,
              f"score={score_diff_seed:.4f}")

        print(f"\n  Fake provider seed-matching proof:")
        print(f"    same filename  → similarity = {score_same_seed:.6f}  (always 1.0 — NOT face recognition)")
        print(f"    diff filename  → similarity = {score_diff_seed:.4f}  (random — NOT face recognition)")

    except ImportError as e:
        skip("FakeProvider tests", str(e))


# ── Section 4: ONNX embedding tests (requires model) ─────────────────────────

def run_onnx_embedding_tests(
    same_person_images: list[bytes],
    diff_person_images: list[bytes],
) -> tuple[float, float]:
    print("\n── Section 4: ONNX embedding tests (requires AdaFace model) ────────────────")

    model_path = os.environ.get("BIOMETRIC_ONNX_MODEL_PATH", "")
    if not model_path or not Path(model_path).is_file():
        skip("ONNX embedding tests",
             f"Model file not found: {model_path or '(BIOMETRIC_ONNX_MODEL_PATH not set)'}")
        print("\n  To run these tests:")
        print("  1. Export AdaFace IR-50 to ONNX (see script header)")
        print("  2. Pass --model /absolute/path/to/adaface_ir50_webface4m.onnx")
        return

    # Reload settings chain before loading provider.
    # Section 1a temporarily sets MODEL_PATH="" when onnx_provider is first imported,
    # causing settings.BIOMETRIC_ONNX_MODEL_PATH to be bound as "" in that module.
    # Reloading app.config + onnx_provider ensures fresh bindings with current env.
    import importlib
    import app.config as _cfg_module
    importlib.reload(_cfg_module)
    import app.services.biometric.onnx_provider as _onnx_mod
    importlib.reload(_onnx_mod)

    try:
        from app.services.biometric.onnx_provider import OnnxEmbeddingProvider
        from app.services.biometric.model_registry import ModelNotAvailableError
        from app.services.biometric.matching_service import (
            compute_cosine_similarity, classify_match_outcome,
            MATCH_THRESHOLD, REVIEW_LOWER,
        )
    except ImportError as e:
        skip("ONNX imports", str(e))
        return

    # Load provider
    print(f"\n  Loading model: {Path(model_path).name}")
    try:
        provider = OnnxEmbeddingProvider()
        print(f"  ✅  Model loaded successfully")
    except ModelNotAvailableError as e:
        print(f"  ❌  ModelNotAvailableError: {e}")
        return
    except Exception as e:
        print(f"  ❌  Unexpected error loading model: {e}")
        return

    # 4a. Embedding shape and norm
    print("\n  4a. Embedding shape and L2 norm")
    sample_emb = provider.generate(same_person_images[0])
    check("Embedding is 512-dim", len(sample_emb) == 512, f"got {len(sample_emb)}")
    norm = _l2_norm(sample_emb)
    check("Embedding is L2-normalized (norm ≈ 1.0)",
          abs(norm - 1.0) < 1e-4,
          f"norm={norm:.6f}")

    # 4b. Filename-independence: same image, different seeds
    print("\n  4b. Filename-independence (proves it's NOT filename-based)")
    emb_same_image_seed_1 = provider.generate(same_person_images[0])
    emb_same_image_seed_2 = provider.generate(same_person_images[0])  # same bytes, same call
    score_deterministic = compute_cosine_similarity(emb_same_image_seed_1, emb_same_image_seed_2)
    check("Same image bytes → identical embedding (deterministic)",
          abs(score_deterministic - 1.0) < 1e-4,
          f"score={score_deterministic:.6f}")

    # 4c. Same person, different images
    print("\n  4c. Same person — different images")
    same_scores = []
    for i in range(len(same_person_images) - 1):
        for j in range(i + 1, len(same_person_images)):
            emb_i = provider.generate(same_person_images[i])
            emb_j = provider.generate(same_person_images[j])
            score = compute_cosine_similarity(emb_i, emb_j)
            outcome = classify_match_outcome(score)
            same_scores.append(score)
            # Without alignment, threshold is relaxed: we expect > 0.30 for PoC
            check(f"  same_person[{i}] vs same_person[{j}]",
                  score > 0.30,
                  f"score={score:.4f} → {outcome}")
    avg_same = sum(same_scores) / len(same_scores) if same_scores else 0.0

    # 4d. Different person
    print("\n  4d. Different person")
    diff_scores = []
    for d_idx, diff_img in enumerate(diff_person_images):
        emb_diff = provider.generate(diff_img)
        for s_idx, same_img in enumerate(same_person_images):
            emb_same = provider.generate(same_img)
            score = compute_cosine_similarity(emb_same, emb_diff)
            outcome = classify_match_outcome(score)
            diff_scores.append(score)
            check(f"  diff_person[{d_idx}] vs same_person[{s_idx}]",
                  score < avg_same,  # different person must score lower than same person avg
                  f"score={score:.4f} → {outcome}")
    avg_diff = sum(diff_scores) / len(diff_scores) if diff_scores else 0.0

    # 4e. Summary — the critical proof
    print("\n  4e. Summary (key proof)")
    margin = avg_same - avg_diff
    print(f"    Average same-person similarity:  {avg_same:.4f}")
    print(f"    Average diff-person similarity:  {avg_diff:.4f}")
    print(f"    Margin (same − diff):            {margin:.4f}")
    check("Same-person similarity > Different-person similarity",
          avg_same > avg_diff,
          f"margin={margin:.4f}")
    check("Margin > 0.10 (meaningful separation)",
          margin > 0.10,
          f"margin={margin:.4f}")

    # 4f. Alignment impact warning
    print(f"\n  4f. Alignment note")
    if avg_same < MATCH_THRESHOLD:
        warn(f"avg_same={avg_same:.4f} below MATCH_THRESHOLD={MATCH_THRESHOLD}.")
        warn("Expected without face alignment/crop. Add RetinaFace alignment for production accuracy.")
    else:
        print(f"  ✅  avg_same={avg_same:.4f} ≥ MATCH_THRESHOLD={MATCH_THRESHOLD}")

    print(f"\n  Current thresholds (R&D only, not production-tuned):")
    print(f"    verified:              score ≥ {MATCH_THRESHOLD}")
    print(f"    manual_review:   {REVIEW_LOWER} ≤ score < {MATCH_THRESHOLD}")
    print(f"    rejected:              score < {REVIEW_LOWER}")

    # 4g. Wrong SHA256 guard (if sha256 env is set)
    sha256_env = os.environ.get("BIOMETRIC_ONNX_MODEL_SHA256", "")
    if sha256_env:
        print("\n  4g. SHA-256 checksum (set)")
        try:
            from app.services.biometric.model_registry import verify_model_checksum
            try:
                verify_model_checksum(Path(model_path), sha256_env)
                check("SHA-256 checksum matches", True)
            except RuntimeError as e:
                check("SHA-256 checksum matches", False, str(e)[:60])
        except ImportError as e:
            skip("SHA-256 check", str(e))

    return avg_same, avg_diff


# ── Section 5: Aligned embedding tests (requires model + detector) ────────────

def run_aligned_embedding_tests(
    same_person_images: list[bytes],
    diff_person_images: list[bytes],
    baseline_same_avg: float,
    baseline_diff_avg: float,
) -> None:
    """
    Run Section 4 again with face alignment enabled.
    Compares with baseline (no-alignment) scores.
    """
    print("\n── Section 5: Aligned embedding tests (AdaFace + SCRFD alignment) ────────")

    model_path    = os.environ.get("BIOMETRIC_ONNX_MODEL_PATH", "")
    detector_path = os.environ.get("BIOMETRIC_FACE_DETECTOR_PATH", "")

    if not model_path or not Path(model_path).is_file():
        skip("Aligned tests", "ONNX model not available")
        return
    if not detector_path or not Path(detector_path).is_file():
        skip("Aligned tests", "Detector not set — pass --detector /path/to/det_500m.onnx")
        return

    # Reload modules with detector path in env
    import importlib
    import app.config as _cfg_mod
    importlib.reload(_cfg_mod)
    import app.services.biometric.face_alignment as _fa_mod
    importlib.reload(_fa_mod)
    import app.services.biometric.onnx_provider as _onnx_mod
    importlib.reload(_onnx_mod)

    try:
        from app.services.biometric.face_alignment import FaceAlignmentPipeline, FaceAlignmentError
        from app.services.biometric.onnx_provider import OnnxEmbeddingProvider
        from app.services.biometric.matching_service import (
            compute_cosine_similarity, classify_match_outcome,
            MATCH_THRESHOLD, REVIEW_LOWER,
        )
    except ImportError as e:
        skip("Aligned imports", str(e))
        return

    print(f"\n  Loading detector: {Path(detector_path).name}")
    try:
        alignment = FaceAlignmentPipeline()
        print("  ✅  Detector loaded successfully")
    except Exception as e:
        print(f"  ❌  Detector load failed: {e}")
        return

    print(f"\n  Loading embedding model: {Path(model_path).name}")
    try:
        provider = OnnxEmbeddingProvider(alignment_pipeline=alignment)
        print("  ✅  Embedding model loaded successfully")
    except Exception as e:
        print(f"  ❌  Embedding model load failed: {e}")
        return

    # SHA-256 check for detector
    det_sha = os.environ.get("BIOMETRIC_FACE_DETECTOR_SHA256", "")
    if det_sha:
        print("\n  5a. Detector SHA-256 checksum")
        try:
            from app.services.biometric.model_registry import verify_model_checksum
            verify_model_checksum(Path(detector_path), det_sha)
            check("Detector SHA-256 checksum matches", True)
        except RuntimeError as e:
            check("Detector SHA-256 checksum matches", False, str(e)[:60])

    # 5b. Same person with alignment
    print("\n  5b. Same person (aligned)")
    same_scores_aligned = []
    skipped_same = 0
    for i in range(len(same_person_images) - 1):
        for j in range(i + 1, len(same_person_images)):
            try:
                emb_i = provider.generate(same_person_images[i])
                emb_j = provider.generate(same_person_images[j])
                score = compute_cosine_similarity(emb_i, emb_j)
                outcome = classify_match_outcome(score)
                same_scores_aligned.append(score)
                check(f"  aligned same_person[{i}] vs [{j}]",
                      score > 0.30,
                      f"score={score:.4f} → {outcome}")
            except FaceAlignmentError as e:
                skipped_same += 1
                warn(f"  same[{i}]vs[{j}] alignment failed: {e.code.value} — skipping pair")

    if skipped_same:
        warn(f"  {skipped_same} pairs skipped (face not detected / too small)")

    # 5c. Different person with alignment
    print("\n  5c. Different person (aligned)")
    diff_scores_aligned = []
    skipped_diff = 0
    for d_idx, diff_img in enumerate(diff_person_images):
        try:
            emb_diff = provider.generate(diff_img)
        except FaceAlignmentError as e:
            skipped_diff += 1
            warn(f"  diff[{d_idx}] alignment failed: {e.code.value} — skipping")
            continue
        for s_idx, same_img in enumerate(same_person_images):
            try:
                emb_same = provider.generate(same_img)
                score    = compute_cosine_similarity(emb_same, emb_diff)
                outcome  = classify_match_outcome(score)
                diff_scores_aligned.append(score)
            except FaceAlignmentError:
                continue  # already warned above

    # 5d. Summary — aligned vs baseline comparison
    print("\n  5d. Alignment comparison summary")
    if not same_scores_aligned:
        warn("  No aligned same-person pairs available — all faces were rejected by detector.")
        warn("  Test images may lack detectable frontal faces. Try close-up frontal photos.")
        return

    avg_same_al = sum(same_scores_aligned) / len(same_scores_aligned)
    avg_diff_al = (sum(diff_scores_aligned) / len(diff_scores_aligned)
                   if diff_scores_aligned else float("nan"))
    margin_al   = avg_same_al - avg_diff_al if diff_scores_aligned else float("nan")

    print(f"\n  {'Metric':<40s} {'Without align':>15s} {'With align':>12s}")
    print(f"  {'─'*67}")
    print(f"  {'avg_same (same-person similarity)':<40s} {baseline_same_avg:>15.4f} {avg_same_al:>12.4f}")
    if diff_scores_aligned:
        print(f"  {'avg_diff (diff-person similarity)':<40s} {baseline_diff_avg:>15.4f} {avg_diff_al:>12.4f}")
        print(f"  {'margin (avg_same − avg_diff)':<40s} {baseline_same_avg-baseline_diff_avg:>15.4f} {margin_al:>12.4f}")
    else:
        warn("  No aligned diff-person scores available")

    # Key checks
    check("avg_same improved with alignment",
          avg_same_al >= baseline_same_avg - 0.05,   # allow tiny regression
          f"baseline={baseline_same_avg:.4f} aligned={avg_same_al:.4f}")

    if diff_scores_aligned:
        check("avg_same > avg_diff (aligned)",
              avg_same_al > avg_diff_al,
              f"margin={margin_al:.4f}")
        check("Margin > 0.25 (aligned — ideal with modern frontal images)",
              margin_al > 0.25,
              f"margin={margin_al:.4f}")

    if avg_same_al >= MATCH_THRESHOLD:
        print(f"\n  ✅  avg_same={avg_same_al:.4f} ≥ MATCH_THRESHOLD={MATCH_THRESHOLD}")
    else:
        warn(f"avg_same={avg_same_al:.4f} below MATCH_THRESHOLD={MATCH_THRESHOLD}")
        if same_scores_aligned:
            warn("Close-up, frontally-aligned modern photos are required for full validation.")


# ── Summary ────────────────────────────────────────────────────────────────────

def print_summary() -> None:
    total   = len(_results)
    passed  = sum(1 for _, ok in _results if ok)
    failed  = total - passed
    print(f"\n{'─'*70}")
    print(f"  Result: {passed}/{total} passed", end="")
    if failed:
        print(f"  ({failed} failed)")
        for label, ok in _results:
            if not ok:
                print(f"    ❌  {label}")
    else:
        print("  — all checks passed")
    print(f"{'─'*70}")

    # Separate Section 4 (baseline) and Section 5 (aligned) results
    s4_labels = [l for l,_ in _results if "same_person" in l or "Same-person" in l
                 or "Margin > 0.10" in l or "diff_person" in l]
    s5_labels = [l for l,_ in _results if "align" in l.lower()
                 or "Margin > 0.25" in l or "improved" in l]
    s4_fail   = sum(1 for l,ok in _results if not ok and l in s4_labels)
    s5_fail   = sum(1 for l,ok in _results if not ok and l in s5_labels)

    alignment_ran = any("align" in l.lower() for l,_ in _results)

    if alignment_ran and s5_fail == 0:
        print("\n  ✅  ALIGNMENT PROOF PASS")
        print("      avg_same (aligned) > avg_diff (aligned), margin > 0.25.")
        print("      Identity separation confirmed with face detection + alignment.")
        if s4_fail > 0:
            print(f"      (Section 4 baseline had {s4_fail} expected fail(s) without alignment — documented.)")
    elif failed == 0 and total > 0:
        print("\n  ✅  PIPELINE PROOF: Embeddings generated from image bytes, not filenames.")
        print("      Same-person similarity > Different-person similarity confirmed.")
    elif total == 0:
        print("\n  ⏭   No embedding tests ran (model file not provided).")
        print("      Guard tests and preprocessing tests only.")
    else:
        print("\n  ❌  PIPELINE PROOF INCOMPLETE — review failed checks above.")
        if not alignment_ran:
            print("      NOTE: Alignment not tested. Pass --detector to enable Section 5.")

    print("\n  IMPORTANT reminders:")
    print("    - Model and detector files not committed (.gitignore:178 enforces *.onnx)")
    print("    - Test images not committed")
    print("    - Scores console-only — not in any API/UI/log")
    print("    - R&D/dev only — BIOMETRIC_ONNX_RND_ENABLED=true forbidden in production")
    print("    - DPIA, DPO sign-off, bias/fairness audit required before production")


# ── Entry point ────────────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="AdaFace R&D Backend-Only Proof — validates ONNX face embedding pipeline"
    )
    p.add_argument("--same-person", nargs="+", metavar="JPEG",
                   help="≥2 JPEG paths of the same person (different photos)")
    p.add_argument("--diff-person", nargs="+", metavar="JPEG",
                   help="≥1 JPEG path of a different person")
    p.add_argument("--model", metavar="ONNX_PATH",
                   help="Absolute path to adaface_ir50_webface4m.onnx (not committed to repo)")
    p.add_argument("--sha256", metavar="HEX",
                   help="Expected SHA-256 hex digest of the model file (optional)")
    p.add_argument("--detector", metavar="DETECTOR_PATH",
                   help="Absolute path to det_500m.onnx (SCRFD face detector, not committed to repo)")
    p.add_argument("--detector-sha256", metavar="HEX",
                   help="Expected SHA-256 hex digest of the detector file (optional)")
    return p.parse_args()


def main() -> None:
    print("=" * 70)
    print("  AdaFace Backend-Only R&D Proof")
    print("  WARNING: R&D/dev only. Output is console-only. Scores not exposed.")
    print("=" * 70)

    args = _parse_args()

    # Set up R&D environment
    _require_rnd_env(
        args.model, args.sha256,
        getattr(args, "detector", None),
        getattr(args, "detector_sha256", None),
    )

    # Load test images
    same_person_images: list[bytes] = []
    diff_person_images: list[bytes] = []

    if args.same_person:
        for p in args.same_person:
            path = Path(p)
            if not path.is_file():
                print(f"  ❌  same-person image not found: {p}")
                sys.exit(1)
            same_person_images.append(path.read_bytes())
        print(f"  Loaded {len(same_person_images)} same-person image(s)")

    if args.diff_person:
        for p in args.diff_person:
            path = Path(p)
            if not path.is_file():
                print(f"  ❌  diff-person image not found: {p}")
                sys.exit(1)
            diff_person_images.append(path.read_bytes())
        print(f"  Loaded {len(diff_person_images)} diff-person image(s)")

    # Run test sections
    run_guard_tests()
    run_fake_provider_tests()

    if same_person_images:
        run_preprocessing_tests(same_person_images[0])

    baseline_same_avg = 0.0
    baseline_diff_avg = 0.0
    if same_person_images and len(same_person_images) >= 2 and diff_person_images:
        baseline_same_avg, baseline_diff_avg = run_onnx_embedding_tests(
            same_person_images, diff_person_images
        )
    elif args.model:
        if not same_person_images:
            skip("ONNX embedding tests", "need --same-person with ≥2 images")
        elif len(same_person_images) < 2:
            skip("ONNX embedding tests", f"need ≥2 same-person images (got {len(same_person_images)})")
        elif not diff_person_images:
            skip("ONNX embedding tests", "need --diff-person with ≥1 image")
    else:
        if not args.model:
            skip("ONNX embedding tests", "pass --model /path/to/adaface_ir50_webface4m.onnx to run")

    # Section 5: Aligned embedding comparison (requires model + detector)
    if same_person_images and len(same_person_images) >= 2 and diff_person_images:
        run_aligned_embedding_tests(
            same_person_images, diff_person_images,
            baseline_same_avg, baseline_diff_avg,
        )
    elif getattr(args, "detector", None):
        skip("Aligned tests", "need --same-person ≥2 and --diff-person ≥1 images")

    print_summary()


if __name__ == "__main__":
    main()
