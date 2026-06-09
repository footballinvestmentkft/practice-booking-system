"""
Unit tests for face detection + alignment pipeline.

All tests are designed to run WITHOUT a detector model file — they test:
  - Constants and config shapes
  - Pure-Python helper functions (sigmoid, anchor generation, NMS, transforms)
  - FaceAlignmentPipeline guard behaviour when model path is not set
  - Preprocessing output shape / dtype / value range on a synthetic image
  - Error codes and sanitized messages

Tests that require the actual det_500m.onnx are marked with
  @pytest.mark.requires_detector
and are skipped unless BIOMETRIC_FACE_DETECTOR_PATH is set in env.
"""
from __future__ import annotations

import io
import math
import os

import numpy as np
import pytest
from PIL import Image

# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_solid_jpeg(w: int = 200, h: int = 200, color=(128, 64, 32)) -> bytes:
    """Return JPEG bytes of a solid-colour image (no real face)."""
    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, format="JPEG")
    return buf.getvalue()


def _make_face_jpeg() -> bytes | None:
    """
    Return face image bytes from the R&D test data directory if available,
    otherwise None (test will be skipped).
    """
    p = os.environ.get("FACE_RND_TESTDATA_SAME1", "")
    if p and os.path.isfile(p):
        return open(p, "rb").read()
    return None


# ── 1. Constants ───────────────────────────────────────────────────────────────

class TestAlignmentConfig:
    def test_arcface_dst_shape(self) -> None:
        from app.services.biometric.face_alignment_config import ARCFACE_DST_5PT
        assert ARCFACE_DST_5PT.shape == (5, 2)

    def test_arcface_dst_dtype(self) -> None:
        from app.services.biometric.face_alignment_config import ARCFACE_DST_5PT
        assert ARCFACE_DST_5PT.dtype == np.float32

    def test_arcface_dst_in_canvas(self) -> None:
        from app.services.biometric.face_alignment_config import ARCFACE_DST_5PT
        assert ARCFACE_DST_5PT[:, 0].min() > 0
        assert ARCFACE_DST_5PT[:, 0].max() < 112
        assert ARCFACE_DST_5PT[:, 1].min() > 0
        assert ARCFACE_DST_5PT[:, 1].max() < 112

    def test_detector_input_size_multiple_of_32(self) -> None:
        from app.services.biometric.face_alignment_config import DETECTOR_INPUT_SIZE
        assert DETECTOR_INPUT_SIZE % 32 == 0
        assert DETECTOR_INPUT_SIZE >= 320


# ── 2. Sigmoid helper ─────────────────────────────────────────────────────────

class TestSigmoid:
    def test_sigmoid_zero(self) -> None:
        from app.services.biometric.face_alignment import _sigmoid
        out = _sigmoid(np.array([0.0], dtype=np.float32))
        assert abs(float(out[0]) - 0.5) < 1e-5

    def test_sigmoid_large_positive(self) -> None:
        from app.services.biometric.face_alignment import _sigmoid
        out = _sigmoid(np.array([88.0], dtype=np.float32))
        assert float(out[0]) > 0.999

    def test_sigmoid_large_negative(self) -> None:
        from app.services.biometric.face_alignment import _sigmoid
        out = _sigmoid(np.array([-88.0], dtype=np.float32))
        assert float(out[0]) < 0.001


# ── 3. Anchor generation ──────────────────────────────────────────────────────

class TestAnchorGeneration:
    def test_anchor_count_stride8(self) -> None:
        from app.services.biometric.face_alignment import _generate_anchor_centers
        from app.services.biometric.face_alignment_config import DETECTOR_INPUT_SIZE, SCRFD_N_ANCHORS
        fsize   = DETECTOR_INPUT_SIZE // 8       # 80
        centers = _generate_anchor_centers(fsize, fsize, 8)
        expected = fsize * fsize * SCRFD_N_ANCHORS   # 12800
        assert centers.shape == (expected, 2)

    def test_anchor_first_center_stride8(self) -> None:
        from app.services.biometric.face_alignment import _generate_anchor_centers
        centers = _generate_anchor_centers(80, 80, 8)
        # First center: (col=0, row=0) → (0, 0)
        assert float(centers[0, 0]) == 0.0
        assert float(centers[0, 1]) == 0.0

    def test_anchor_second_cell_x(self) -> None:
        from app.services.biometric.face_alignment import _generate_anchor_centers
        from app.services.biometric.face_alignment_config import SCRFD_N_ANCHORS
        centers = _generate_anchor_centers(80, 80, 8)
        # After SCRFD_N_ANCHORS anchors at (0,0), next cell is at x=8
        stride_idx = SCRFD_N_ANCHORS
        assert float(centers[stride_idx, 0]) == 8.0
        assert float(centers[stride_idx, 1]) == 0.0


# ── 4. NMS ────────────────────────────────────────────────────────────────────

class TestNMS:
    def test_nms_keeps_best(self) -> None:
        from app.services.biometric.face_alignment import _nms
        boxes  = np.array([[0,0,10,10],[1,1,11,11]], dtype=np.float32)
        scores = np.array([0.9, 0.8], dtype=np.float32)
        keep   = _nms(boxes, scores, iou_threshold=0.3)
        assert len(keep) == 1
        assert int(keep[0]) == 0  # higher score kept

    def test_nms_keeps_non_overlapping(self) -> None:
        from app.services.biometric.face_alignment import _nms
        boxes  = np.array([[0,0,10,10],[100,100,110,110]], dtype=np.float32)
        scores = np.array([0.9, 0.8], dtype=np.float32)
        keep   = _nms(boxes, scores, iou_threshold=0.3)
        assert len(keep) == 2

    def test_nms_empty(self) -> None:
        from app.services.biometric.face_alignment import _nms
        boxes  = np.zeros((0, 4), dtype=np.float32)
        scores = np.zeros((0,),   dtype=np.float32)
        keep   = _nms(boxes, scores, iou_threshold=0.3)
        assert len(keep) == 0


# ── 5. Similarity transform ───────────────────────────────────────────────────

class TestSimilarityTransform:
    def test_identity_transform(self) -> None:
        """When src == dst the transform should be the identity."""
        from app.services.biometric.face_alignment import _estimate_similarity_transform
        from app.services.biometric.face_alignment_config import ARCFACE_DST_5PT
        M = _estimate_similarity_transform(ARCFACE_DST_5PT, ARCFACE_DST_5PT)
        assert M.shape == (2, 3)
        # M ≈ [[1, 0, 0], [0, 1, 0]]
        assert abs(float(M[0, 0]) - 1.0) < 1e-3
        assert abs(float(M[1, 1]) - 1.0) < 1e-3
        assert abs(float(M[0, 1]))        < 1e-3
        assert abs(float(M[0, 2]))        < 1e-1
        assert abs(float(M[1, 2]))        < 1e-1

    def test_scale_transform(self) -> None:
        """Uniform scale=2 from (10,10),(20,10) to (20,20),(40,20) should give M≈[[2,0,0],[0,2,0]]."""
        from app.services.biometric.face_alignment import _estimate_similarity_transform
        src = np.array([[10.0,10.0],[20.0,10.0],[15.0,15.0],[12.0,18.0],[18.0,18.0]],
                       dtype=np.float32)
        dst = src * 2.0
        M   = _estimate_similarity_transform(src, dst)
        scale = math.sqrt(float(M[0,0])**2 + float(M[1,0])**2)
        assert abs(scale - 2.0) < 0.05

    def test_output_shape(self) -> None:
        from app.services.biometric.face_alignment import _estimate_similarity_transform
        from app.services.biometric.face_alignment_config import ARCFACE_DST_5PT
        M = _estimate_similarity_transform(ARCFACE_DST_5PT, ARCFACE_DST_5PT)
        assert M.shape == (2, 3)
        assert M.dtype == np.float64


# ── 6. Warp crop ──────────────────────────────────────────────────────────────

class TestWarpCrop:
    def test_output_size(self) -> None:
        from app.services.biometric.face_alignment import _warp_aligned_crop
        from app.services.biometric.face_alignment_config import ARCFACE_DST_5PT
        from app.services.biometric.face_alignment import _estimate_similarity_transform
        img = Image.new("RGB", (200, 200), (128, 64, 32))
        src = ARCFACE_DST_5PT * 1.5 + 10  # some offset
        M   = _estimate_similarity_transform(src, ARCFACE_DST_5PT)
        out = _warp_aligned_crop(img, M, size=112)
        assert out.size == (112, 112)
        assert out.mode == "RGB"

    def test_output_is_pil(self) -> None:
        from app.services.biometric.face_alignment import (
            _warp_aligned_crop, _estimate_similarity_transform,
        )
        from app.services.biometric.face_alignment_config import ARCFACE_DST_5PT
        img = Image.new("RGB", (300, 300), (64, 64, 64))
        M   = _estimate_similarity_transform(ARCFACE_DST_5PT, ARCFACE_DST_5PT)
        out = _warp_aligned_crop(img, M, size=112)
        assert isinstance(out, Image.Image)


# ── 7. FaceAlignmentPipeline guards ──────────────────────────────────────────

class TestFaceAlignmentPipelineGuards:
    def test_no_detector_path_raises(self, monkeypatch) -> None:
        """Pipeline raises ModelNotAvailableError when path is not set."""
        monkeypatch.setenv("BIOMETRIC_FACE_DETECTOR_PATH", "")
        import importlib, app.config as _cfg
        importlib.reload(_cfg)
        from app.services.biometric.model_registry import ModelNotAvailableError
        import app.services.biometric.face_alignment as _fa
        importlib.reload(_fa)
        with pytest.raises(ModelNotAvailableError):
            _fa.FaceAlignmentPipeline()

    def test_nonexistent_path_raises(self, monkeypatch) -> None:
        monkeypatch.setenv("BIOMETRIC_FACE_DETECTOR_PATH", "/nonexistent/det_500m.onnx")
        import importlib, app.config as _cfg
        importlib.reload(_cfg)
        from app.services.biometric.model_registry import ModelNotAvailableError
        import app.services.biometric.face_alignment as _fa
        importlib.reload(_fa)
        with pytest.raises(ModelNotAvailableError):
            _fa.FaceAlignmentPipeline()

    def test_get_pipeline_returns_none_without_path(self, monkeypatch) -> None:
        monkeypatch.setenv("BIOMETRIC_FACE_DETECTOR_PATH", "")
        import importlib, app.config as _cfg
        importlib.reload(_cfg)
        import app.services.biometric.face_alignment as _fa
        importlib.reload(_fa)
        result = _fa.get_face_alignment_pipeline()
        assert result is None


# ── 8. Invalid image guard ────────────────────────────────────────────────────

class TestFaceAlignmentPipelineImageGuard:
    def test_invalid_image_raises_face_alignment_error(self) -> None:
        from app.services.biometric.face_alignment import (
            FaceAlignmentPipeline, FaceAlignmentError, AlignedFaceErrorCode,
        )
        pipeline = _make_fake_pipeline_without_detector()
        if pipeline is None:
            pytest.skip("Cannot create pipeline without detector model")
        with pytest.raises(FaceAlignmentError) as exc_info:
            pipeline._decode_image(b"not_an_image_0xDEADBEEF")
        assert exc_info.value.code == AlignedFaceErrorCode.INVALID_IMAGE

    def test_no_face_raises(self) -> None:
        from app.services.biometric.face_alignment import (
            FaceAlignmentError, AlignedFaceErrorCode,
        )
        pipeline = _make_fake_pipeline_without_detector()
        if pipeline is None:
            pytest.skip("Cannot create pipeline without detector model")
        # _select_face with empty arrays raises NO_FACE_DETECTED
        with pytest.raises(FaceAlignmentError) as exc_info:
            import numpy as np
            pipeline._select_face(
                np.zeros((0, 4), dtype=np.float32),
                np.zeros((0,),   dtype=np.float32),
                np.zeros((0, 5, 2), dtype=np.float32),
            )
        assert exc_info.value.code == AlignedFaceErrorCode.NO_FACE_DETECTED

    def test_face_too_small_raises(self) -> None:
        from app.services.biometric.face_alignment import (
            FaceAlignmentError, AlignedFaceErrorCode,
        )
        from app.services.biometric.face_alignment_config import MIN_FACE_PX
        pipeline = _make_fake_pipeline_without_detector()
        if pipeline is None:
            pytest.skip("Cannot create pipeline without detector model")
        tiny_box = np.array([[0, 0, MIN_FACE_PX - 1, MIN_FACE_PX - 1]], dtype=np.float32)
        kps = np.zeros((1, 5, 2), dtype=np.float32)
        with pytest.raises(FaceAlignmentError) as exc_info:
            pipeline._select_face(tiny_box, np.array([0.9]), kps)
        assert exc_info.value.code == AlignedFaceErrorCode.FACE_TOO_SMALL

    def test_no_landmark_coords_in_error_message(self) -> None:
        """Error messages must not contain raw landmark coordinates."""
        from app.services.biometric.face_alignment import (
            FaceAlignmentError, AlignedFaceErrorCode,
        )
        err = FaceAlignmentError(AlignedFaceErrorCode.NO_FACE_DETECTED)
        assert "landmark" not in str(err).lower() or "missing" in str(err).lower()
        # No coordinates: message must not contain brackets like [[1.2, 3.4]]
        assert "[[" not in str(err)


# ── 9. Preprocessing tensor output (no detector required) ────────────────────

class TestAlignmentPipelineTensorOutput:
    def test_to_tensor_shape(self) -> None:
        from app.services.biometric.face_alignment import FaceAlignmentPipeline
        img = Image.new("RGB", (112, 112), (64, 128, 192))
        arr = FaceAlignmentPipeline._to_tensor(img)
        assert arr.shape == (1, 3, 112, 112)

    def test_to_tensor_dtype(self) -> None:
        from app.services.biometric.face_alignment import FaceAlignmentPipeline
        img = Image.new("RGB", (112, 112), (255, 255, 255))
        arr = FaceAlignmentPipeline._to_tensor(img)
        assert arr.dtype == np.float32

    def test_to_tensor_value_range(self) -> None:
        from app.services.biometric.face_alignment import FaceAlignmentPipeline
        img = Image.new("RGB", (112, 112), (0, 127, 255))
        arr = FaceAlignmentPipeline._to_tensor(img)
        assert float(arr.min()) >= -1.01
        assert float(arr.max()) <=  1.01


# ── 10. Detector-required tests (skipped without model) ──────────────────────

@pytest.mark.skipif(
    not os.environ.get("BIOMETRIC_FACE_DETECTOR_PATH"),
    reason="BIOMETRIC_FACE_DETECTOR_PATH not set — skipping live detector tests",
)
class TestFaceDetectorLive:
    def setup_method(self) -> None:
        """Reload settings so env vars set by guard-test monkeypatches are cleared."""
        import importlib, app.config as _cfg
        importlib.reload(_cfg)
        import app.services.biometric.face_alignment as _fa
        importlib.reload(_fa)

    def test_detector_loads(self) -> None:
        from app.services.biometric.face_alignment import FaceAlignmentPipeline
        pipeline = FaceAlignmentPipeline()
        assert pipeline._session is not None

    def test_detect_no_face_in_solid_image(self) -> None:
        from app.services.biometric.face_alignment import FaceAlignmentPipeline
        pipeline = FaceAlignmentPipeline()
        img = Image.new("RGB", (300, 300), (64, 64, 64))
        boxes, scores, kps = pipeline._detect_faces(img)
        # Solid colour — any detections should be very low confidence (near-noise).
        # A genuine face detection would score ≥ 0.80; allow low-score noise.
        if boxes.shape[0] > 0:
            assert scores.max() < 0.80, (
                f"Unexpected high-confidence ({scores.max():.4f}) detection in solid image"
            )

    def test_preprocess_solid_raises_no_face(self) -> None:
        from app.services.biometric.face_alignment import (
            FaceAlignmentPipeline, FaceAlignmentError, AlignedFaceErrorCode,
        )
        pipeline  = FaceAlignmentPipeline()
        jpeg      = _make_solid_jpeg()
        with pytest.raises(FaceAlignmentError) as exc_info:
            pipeline.preprocess(jpeg)
        assert exc_info.value.code in (
            AlignedFaceErrorCode.NO_FACE_DETECTED,
            AlignedFaceErrorCode.FACE_TOO_SMALL,
        )

    def test_preprocess_face_image_output_shape(self) -> None:
        from app.services.biometric.face_alignment import FaceAlignmentPipeline
        face_bytes = _make_face_jpeg()
        if face_bytes is None:
            pytest.skip("No face image available (set FACE_RND_TESTDATA_SAME1)")
        pipeline = FaceAlignmentPipeline()
        tensor   = pipeline.preprocess(face_bytes)
        assert tensor.shape == (1, 3, 112, 112)
        assert tensor.dtype == np.float32
        assert float(tensor.min()) >= -1.01
        assert float(tensor.max()) <=  1.01


# ── Private helpers ───────────────────────────────────────────────────────────

def _make_fake_pipeline_without_detector():
    """
    Create a FaceAlignmentPipeline instance that bypasses __init__
    so we can test individual methods without a detector model.
    """
    from app.services.biometric.face_alignment import FaceAlignmentPipeline
    import types
    p = object.__new__(FaceAlignmentPipeline)
    p._session = None
    p._alignment_pipeline = None
    return p
