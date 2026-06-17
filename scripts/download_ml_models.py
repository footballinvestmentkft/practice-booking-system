#!/usr/bin/env python3
"""
Download ML models for the analysis pipeline.

Usage:
  python scripts/download_ml_models.py --model ssd-mobilenet-v1-coco
  python scripts/download_ml_models.py --list

Models are stored in app/ml_models/ (gitignored via *.onnx).
Each model has a SHA256 hash check — download is rejected if the hash
does not match (supply-chain protection).

Override the download URL via environment variable:
  BALL_DETECTION_MODEL_URL=https://internal.s3/models/ssd_mobilenet_v1_12.onnx

This script is NOT a runtime dependency. It runs once during deployment
setup or local dev bootstrapping. Production uses pre-placed model files.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import sys
import urllib.request
from pathlib import Path

_TARGET_DIR = Path(__file__).resolve().parent.parent / "app" / "ml_models"

_MODELS: dict[str, dict] = {
    "ssd-mobilenet-v1-coco": {
        "filename": "ssd_mobilenet_v1_12.onnx",
        "default_url": (
            "https://huggingface.co/onnxmodelzoo/ssd_mobilenet_v1_12/"
            "resolve/main/ssd_mobilenet_v1_12.onnx"
        ),
        "env_url_key": "BALL_DETECTION_MODEL_URL",
        "sha256": "b8fba5e404077d4048d27fcd1667e85e27e192eb9bf51e696c46a3acd7d21058",
        "size_mb_approx": 29.5,
        "licence": "Apache-2.0 (TensorFlow Model Zoo / ONNX Model Zoo)",
        "description": "SSD MobileNet v1 COCO — sports_ball class 37",
    },
}


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def _download(model_key: str) -> None:
    spec = _MODELS[model_key]
    dest = _TARGET_DIR / spec["filename"]

    url = os.environ.get(spec["env_url_key"]) or spec["default_url"]

    if dest.exists():
        print(f"File already exists: {dest}")
        actual_hash = _sha256(dest)
        if spec["sha256"] and actual_hash != spec["sha256"]:
            print(f"  WARNING: SHA256 mismatch!")
            print(f"  Expected: {spec['sha256']}")
            print(f"  Actual:   {actual_hash}")
            sys.exit(1)
        elif spec["sha256"]:
            print(f"  SHA256 verified: {actual_hash}")
        else:
            print(f"  SHA256 (record this): {actual_hash}")
        return

    _TARGET_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Downloading {model_key}...")
    print(f"  URL: {url}")
    print(f"  Destination: {dest}")
    print(f"  Licence: {spec['licence']}")

    tmp = dest.with_suffix(".onnx.tmp")
    try:
        urllib.request.urlretrieve(url, str(tmp))
    except Exception as e:
        tmp.unlink(missing_ok=True)
        print(f"  FAILED: {e}")
        sys.exit(1)

    actual_hash = _sha256(tmp)
    if spec["sha256"] and actual_hash != spec["sha256"]:
        tmp.unlink()
        print(f"  SHA256 MISMATCH — file deleted for safety.")
        print(f"  Expected: {spec['sha256']}")
        print(f"  Actual:   {actual_hash}")
        sys.exit(1)

    tmp.rename(dest)
    print(f"  Downloaded: {dest} ({dest.stat().st_size / 1_048_576:.1f} MB)")
    print(f"  SHA256: {actual_hash}")
    if not spec["sha256"]:
        print(f"  NOTE: No expected hash was set. Record the hash above in this script.")


def _list_models() -> None:
    print("Available models:\n")
    for key, spec in _MODELS.items():
        print(f"  {key}")
        print(f"    File:    {spec['filename']}")
        print(f"    Size:    ~{spec['size_mb_approx']} MB")
        print(f"    Licence: {spec['licence']}")
        print(f"    Desc:    {spec['description']}")
        dest = _TARGET_DIR / spec["filename"]
        print(f"    Status:  {'PRESENT' if dest.exists() else 'NOT DOWNLOADED'}")
        print()


def main() -> None:
    parser = argparse.ArgumentParser(description="Download ML models for analysis pipeline")
    parser.add_argument("--model", type=str, help="Model key to download")
    parser.add_argument("--list", action="store_true", help="List available models")
    args = parser.parse_args()

    if args.list:
        _list_models()
        return

    if not args.model:
        parser.print_help()
        sys.exit(1)

    if args.model not in _MODELS:
        print(f"Unknown model: {args.model}")
        print(f"Available: {', '.join(_MODELS.keys())}")
        sys.exit(1)

    _download(args.model)


if __name__ == "__main__":
    main()
