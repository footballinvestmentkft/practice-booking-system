"""
ONNX embedding provider tests — PR-5 R&D/prototype.

BOP-01  OnnxEmbeddingProvider disabled by default (BIOMETRIC_ONNX_RND_ENABLED=false → error)
BOP-02  get_embedding_provider("onnx") without RND flag → ModelNotAvailableError
BOP-03  onnxruntime NOT imported when provider=fake (import isolation)
BOP-04  OnnxEmbeddingProvider with mocked InferenceSession → 512-dim float list
BOP-05  preprocess_face_image returns (1, 3, 112, 112) float32 NCHW tensor
BOP-06  preprocess_face_image normalizes to [-1, 1] range
BOP-07  verify_model_checksum: correct checksum → passes silently
BOP-08  verify_model_checksum: wrong checksum → RuntimeError
BOP-09  assert_model_path_safe: empty path → ModelNotAvailableError
BOP-10  assert_model_path_safe: file not found → ModelNotAvailableError
BOP-11  OnnxEmbeddingProvider: model path empty → ModelNotAvailableError
BOP-12  generate() output: L2-normalized unit vector (norm ≈ 1.0)
BOP-13  generate() output: no face_match_score field
BOP-14  onnx_provider module: onnxruntime import is deferred (not at module-level)
BOP-15  face_preprocessing module: no onnxruntime import
"""
from __future__ import annotations

import ast
import hashlib
import io
import struct
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
from PIL import Image

from app.services.biometric.model_registry import (
    ModelNotAvailableError,
    assert_model_path_safe,
    verify_model_checksum,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_fake_image_bytes(size: tuple = (160, 160)) -> bytes:
    """Create minimal valid JPEG bytes for testing preprocessing."""
    img = Image.new("RGB", size, color=(128, 64, 32))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def _make_mock_session(embed_dim: int = 512) -> MagicMock:
    mock = MagicMock()
    mock.run.return_value = [np.array([[0.01] * embed_dim], dtype=np.float32)]
    mock.get_inputs.return_value = [MagicMock(name="input")]
    return mock


# ── BOP-01 — default disabled ─────────────────────────────────────────────────

def test_bop01_onnx_provider_disabled_by_default(monkeypatch):
    monkeypatch.setattr("app.config.settings.BIOMETRIC_ONNX_RND_ENABLED", False)
    monkeypatch.setattr("app.config.settings.BIOMETRIC_FACE_MATCHING_ENABLED", True)
    from app.services.biometric.onnx_provider import OnnxEmbeddingProvider
    with pytest.raises(ModelNotAvailableError, match="BIOMETRIC_ONNX_RND_ENABLED"):
        OnnxEmbeddingProvider()


# ── BOP-02 — get_embedding_provider guard ─────────────────────────────────────

def test_bop02_get_provider_onnx_without_rnd_flag_raises(monkeypatch, fake_provider_enabled):
    monkeypatch.setattr("app.config.settings.BIOMETRIC_ONNX_RND_ENABLED", False)
    monkeypatch.setattr("app.config.settings.BIOMETRIC_EMBEDDING_PROVIDER", "onnx")
    from app.services.biometric.embedding_service import get_embedding_provider
    with pytest.raises(ModelNotAvailableError, match="BIOMETRIC_ONNX_RND_ENABLED"):
        get_embedding_provider()


# ── BOP-03 — onnxruntime not imported when provider=fake ─────────────────────

def test_bop03_onnxruntime_not_imported_for_fake_provider(fake_provider_enabled):
    from app.services.biometric.embedding_service import get_embedding_provider
    # Remove onnxruntime from modules cache if present
    ort_key = next((k for k in sys.modules if k.startswith("onnxruntime")), None)
    pre_loaded = ort_key is not None

    get_embedding_provider()  # should use FakeEmbeddingProvider

    ort_key_after = next((k for k in sys.modules if k.startswith("onnxruntime")), None)
    if not pre_loaded:
        assert ort_key_after is None, "onnxruntime must NOT be imported for fake provider"


# ── BOP-04 — mocked session → 512-dim output ─────────────────────────────────

def test_bop04_mocked_session_512_dim(monkeypatch, tmp_path):
    model_file = tmp_path / "model.onnx"
    model_file.write_bytes(b"fake_onnx_model_data")

    monkeypatch.setattr("app.config.settings.BIOMETRIC_ONNX_RND_ENABLED", True)
    monkeypatch.setattr("app.config.settings.BIOMETRIC_FACE_MATCHING_ENABLED", True)
    monkeypatch.setattr("app.config.settings.BIOMETRIC_ONNX_MODEL_PATH", str(model_file))
    monkeypatch.setattr("app.config.settings.BIOMETRIC_ONNX_MODEL_SHA256", "")

    mock_sess = _make_mock_session(512)

    with patch("onnxruntime.InferenceSession", return_value=mock_sess):
        from app.services.biometric.onnx_provider import OnnxEmbeddingProvider
        provider = OnnxEmbeddingProvider()
        result = provider.generate(_make_fake_image_bytes())

    assert isinstance(result, list)
    assert len(result) == 512
    assert all(isinstance(v, float) for v in result)


# ── BOP-05 — preprocess shape ─────────────────────────────────────────────────

def test_bop05_preprocess_returns_nchw_shape():
    from app.services.biometric.face_preprocessing import preprocess_face_image
    out = preprocess_face_image(_make_fake_image_bytes())
    assert out.shape == (1, 3, 112, 112)
    assert out.dtype == np.float32


# ── BOP-06 — preprocess normalization range ───────────────────────────────────

def test_bop06_preprocess_normalized_range():
    from app.services.biometric.face_preprocessing import preprocess_face_image
    out = preprocess_face_image(_make_fake_image_bytes())
    assert out.min() >= -1.01
    assert out.max() <= 1.01


# ── BOP-07 — checksum correct ─────────────────────────────────────────────────

def test_bop07_checksum_correct(tmp_path):
    data = b"model_content_for_test"
    fpath = tmp_path / "model.onnx"
    fpath.write_bytes(data)
    expected = hashlib.sha256(data).hexdigest()
    verify_model_checksum(fpath, expected)  # should not raise


# ── BOP-08 — checksum wrong → RuntimeError ────────────────────────────────────

def test_bop08_checksum_wrong_raises(tmp_path):
    fpath = tmp_path / "model.onnx"
    fpath.write_bytes(b"legitimate_model")
    with pytest.raises(RuntimeError, match="checksum mismatch"):
        verify_model_checksum(fpath, "a" * 64)


# ── BOP-09 — empty path → ModelNotAvailableError ─────────────────────────────

def test_bop09_empty_path_raises():
    with pytest.raises(ModelNotAvailableError, match="not set"):
        assert_model_path_safe("")


# ── BOP-10 — file not found → ModelNotAvailableError ─────────────────────────

def test_bop10_file_not_found_raises(tmp_path):
    with pytest.raises(ModelNotAvailableError, match="not found"):
        assert_model_path_safe(str(tmp_path / "nonexistent.onnx"))


# ── BOP-11 — model path empty → ModelNotAvailableError ───────────────────────

def test_bop11_onnx_provider_empty_model_path_raises(monkeypatch, tmp_path):
    monkeypatch.setattr("app.config.settings.BIOMETRIC_ONNX_RND_ENABLED", True)
    monkeypatch.setattr("app.config.settings.BIOMETRIC_FACE_MATCHING_ENABLED", True)
    monkeypatch.setattr("app.config.settings.BIOMETRIC_ONNX_MODEL_PATH", "")
    from app.services.biometric.onnx_provider import OnnxEmbeddingProvider
    with patch("onnxruntime.InferenceSession"):
        with pytest.raises(ModelNotAvailableError):
            OnnxEmbeddingProvider()


# ── BOP-12 — output is unit vector ───────────────────────────────────────────

def test_bop12_generate_output_unit_vector(monkeypatch, tmp_path):
    model_file = tmp_path / "model.onnx"
    model_file.write_bytes(b"fake_data")

    monkeypatch.setattr("app.config.settings.BIOMETRIC_ONNX_RND_ENABLED", True)
    monkeypatch.setattr("app.config.settings.BIOMETRIC_FACE_MATCHING_ENABLED", True)
    monkeypatch.setattr("app.config.settings.BIOMETRIC_ONNX_MODEL_PATH", str(model_file))
    monkeypatch.setattr("app.config.settings.BIOMETRIC_ONNX_MODEL_SHA256", "")

    # Non-unit raw output — should be normalized
    mock_sess = MagicMock()
    mock_sess.run.return_value = [np.array([[2.0] * 512], dtype=np.float32)]
    mock_sess.get_inputs.return_value = [MagicMock(name="input")]

    with patch("onnxruntime.InferenceSession", return_value=mock_sess):
        from app.services.biometric.onnx_provider import OnnxEmbeddingProvider
        provider = OnnxEmbeddingProvider()
        result = provider.generate(_make_fake_image_bytes())

    norm = sum(v * v for v in result) ** 0.5
    assert abs(norm - 1.0) < 1e-4, f"Output should be unit vector, norm={norm}"


# ── BOP-13 — no face_match_score in output ───────────────────────────────────

def test_bop13_generate_no_face_match_score(monkeypatch, tmp_path):
    model_file = tmp_path / "model.onnx"
    model_file.write_bytes(b"fake_data")

    monkeypatch.setattr("app.config.settings.BIOMETRIC_ONNX_RND_ENABLED", True)
    monkeypatch.setattr("app.config.settings.BIOMETRIC_FACE_MATCHING_ENABLED", True)
    monkeypatch.setattr("app.config.settings.BIOMETRIC_ONNX_MODEL_PATH", str(model_file))
    monkeypatch.setattr("app.config.settings.BIOMETRIC_ONNX_MODEL_SHA256", "")

    with patch("onnxruntime.InferenceSession", return_value=_make_mock_session()):
        from app.services.biometric.onnx_provider import OnnxEmbeddingProvider
        provider = OnnxEmbeddingProvider()
        result = provider.generate(_make_fake_image_bytes())

    assert isinstance(result, list), "generate() must return a plain list, not a dict"
    # Plain list has no 'face_match_score' attribute
    assert not hasattr(result, "face_match_score")


# ── BOP-14 — onnxruntime is deferred import in onnx_provider ─────────────────

def test_bop14_onnxruntime_deferred_import_in_onnx_provider():
    import app.services.biometric.onnx_provider as mod
    src = open(mod.__file__).read()
    tree = ast.parse(src)
    # Top-level imports must not include onnxruntime
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [a.name for a in node.names]
            module = getattr(node, "module", "") or ""
            assert "onnxruntime" not in module, "onnxruntime must not be a top-level import"
            assert not any("onnxruntime" in n for n in names)


# ── BOP-15 — face_preprocessing has no onnxruntime ───────────────────────────

def test_bop15_face_preprocessing_no_onnxruntime():
    import app.services.biometric.face_preprocessing as mod
    import inspect
    src = inspect.getsource(mod)
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [a.name for a in node.names]
            module = getattr(node, "module", "") or ""
            assert "onnxruntime" not in module
            assert not any("onnxruntime" in n for n in names)