"""
AdaFace IR-50 WebFace4M → ONNX export script.

Loads CVLFace pretrained weights (local_models/cvlface_adaface_ir50_webface4m/)
and exports the backbone to ONNX format compatible with OnnxEmbeddingProvider.

Usage:
  python scripts/adaface_onnx_export.py

Output:
  local_models/adaface_ir50_webface4m.onnx  (≈170 MB)

R&D only — model weight not committed (*.onnx, *.ckpt in .gitignore).
"""
from __future__ import annotations

import math
import pathlib
import sys

import torch
import torch.nn as nn
from torch.nn import (
    BatchNorm1d, BatchNorm2d, Conv2d, Dropout, Flatten,
    Linear, MaxPool2d, Module, PReLU, ReLU, Sequential, Sigmoid,
)

# ── Inline Backbone definition (from models/iresnet/model.py, fvcore removed) ──
# fvcore is not available in this venv. The class definition is identical;
# only the flop_count call in __main__ used fvcore — that block is unused here.

def _initialize_weights(modules):
    for m in modules:
        if isinstance(m, nn.Conv2d):
            nn.init.kaiming_normal_(m.weight, mode="fan_out", nonlinearity="relu")
            if m.bias is not None:
                m.bias.data.zero_()
        elif isinstance(m, nn.BatchNorm2d):
            m.weight.data.fill_(1)
            m.bias.data.zero_()
        elif isinstance(m, nn.Linear):
            nn.init.kaiming_normal_(m.weight, mode="fan_out", nonlinearity="relu")
            if m.bias is not None:
                m.bias.data.zero_()


class _SEModule(Module):
    def __init__(self, channels, reduction):
        super().__init__()
        self.avg_pool = nn.AdaptiveAvgPool2d(1)
        self.fc1 = Conv2d(channels, channels // reduction, kernel_size=1, padding=0, bias=False)
        nn.init.xavier_uniform_(self.fc1.weight.data)
        self.relu = ReLU(inplace=True)
        self.fc2 = Conv2d(channels // reduction, channels, kernel_size=1, padding=0, bias=False)
        self.sigmoid = Sigmoid()

    def forward(self, x):
        m = self.avg_pool(x)
        m = self.fc1(m)
        m = self.relu(m)
        m = self.fc2(m)
        m = self.sigmoid(m)
        return x * m


class _BasicBlockIR(Module):
    def __init__(self, in_channel, depth, stride):
        super().__init__()
        if in_channel == depth:
            self.shortcut_layer = MaxPool2d(1, stride)
        else:
            self.shortcut_layer = Sequential(
                Conv2d(in_channel, depth, (1, 1), stride, bias=False),
                BatchNorm2d(depth),
            )
        self.res_layer = Sequential(
            BatchNorm2d(in_channel),
            Conv2d(in_channel, depth, (3, 3), (1, 1), 1, bias=False),
            BatchNorm2d(depth),
            PReLU(depth),
            Conv2d(depth, depth, (3, 3), stride, 1, bias=False),
            BatchNorm2d(depth),
        )

    def forward(self, x):
        return self.res_layer(x) + self.shortcut_layer(x)


def _get_blocks(num_layers: int):
    from collections import namedtuple
    Bottleneck = namedtuple("Block", ["in_channel", "depth", "stride"])

    def get_block(in_channel, depth, num_units, stride=2):
        return [Bottleneck(in_channel, depth, stride)] + [
            Bottleneck(depth, depth, 1) for _ in range(num_units - 1)
        ]

    if num_layers == 50:
        return [
            get_block(64, 64, 3),
            get_block(64, 128, 4),
            get_block(128, 256, 14),
            get_block(256, 512, 3),
        ]
    raise ValueError(f"Only IR-50 supported here; got num_layers={num_layers}")


class _Backbone(Module):
    def __init__(self, input_size, num_layers=50, output_dim=512):
        super().__init__()
        assert input_size[0] == 112
        self.input_layer = Sequential(
            Conv2d(3, 64, (3, 3), 1, 1, bias=False),
            BatchNorm2d(64),
            PReLU(64),
        )
        blocks = _get_blocks(num_layers)
        modules = [
            _BasicBlockIR(b.in_channel, b.depth, b.stride)
            for block in blocks
            for b in block
        ]
        self.body = Sequential(*modules)
        self.output_layer = Sequential(
            BatchNorm2d(512),
            Dropout(0.4),
            Flatten(),
            Linear(512 * 7 * 7, output_dim),
            BatchNorm1d(output_dim, affine=False),
        )
        _initialize_weights(self.modules())

    def forward(self, x):
        x = self.input_layer(x)
        x = self.body(x)
        x = self.output_layer(x)
        return x


# ── Export logic ───────────────────────────────────────────────────────────────

REPO_DIR  = pathlib.Path("local_models/cvlface_adaface_ir50_webface4m")
CKPT_PATH = REPO_DIR / "pretrained_model" / "model.pt"
OUT_PATH  = pathlib.Path("local_models/adaface_ir50_webface4m.onnx")


def main() -> None:
    print("=" * 60)
    print("  AdaFace IR-50 WebFace4M → ONNX export")
    print("  R&D only — model not committed (.gitignore)")
    print("=" * 60)

    # 1. Load state dict
    if not CKPT_PATH.is_file():
        print(f"\n  ERROR: checkpoint not found: {CKPT_PATH}")
        sys.exit(1)

    print(f"\n  Loading: {CKPT_PATH}  ({CKPT_PATH.stat().st_size / 1_048_576:.1f} MB)")
    state_dict = torch.load(str(CKPT_PATH), map_location="cpu", weights_only=True)
    print(f"  Keys: {len(state_dict)}")

    # 2. Strip 'net.' prefix (CVLFace wraps backbone in self.net)
    stripped = {}
    for k, v in state_dict.items():
        new_k = k[4:] if k.startswith("net.") else k
        stripped[new_k] = v

    print(f"  Keys after strip: {len(stripped)}")
    sample = list(stripped.items())[:3]
    for k, v in sample:
        print(f"    {k}: {tuple(v.shape)}")

    # 3. Build backbone and load weights
    print("\n  Building IR-50 backbone...")
    model = _Backbone(input_size=[112, 112], num_layers=50, output_dim=512)
    missing, unexpected = model.load_state_dict(stripped, strict=True)
    if missing:
        print(f"  ⚠️  Missing keys: {missing[:5]}")
    if unexpected:
        print(f"  ⚠️  Unexpected keys: {unexpected[:5]}")
    if not missing and not unexpected:
        print("  ✅  State dict loaded — all keys matched")

    model.eval()

    # 4. Sanity-check forward pass
    print("\n  Sanity check (forward pass)...")
    with torch.no_grad():
        dummy = torch.randn(1, 3, 112, 112)
        out = model(dummy)
    assert out.shape == (1, 512), f"Expected (1,512) got {out.shape}"
    norm = float(out.norm(dim=1).item())
    print(f"  Output shape: {tuple(out.shape)}")
    print(f"  Output L2 norm (before explicit normalization): {norm:.4f}")
    print("  ✅  Forward pass OK")

    # 5. ONNX export
    OUT_PATH.parent.mkdir(exist_ok=True)
    print(f"\n  Exporting to ONNX: {OUT_PATH}")
    with torch.no_grad():
        torch.onnx.export(
            model,
            dummy,
            str(OUT_PATH),
            input_names=["input"],
            output_names=["output"],
            dynamic_axes={"input": {0: "batch"}, "output": {0: "batch"}},
            opset_version=14,
            do_constant_folding=True,
        )

    size_mb = OUT_PATH.stat().st_size / 1_048_576
    print(f"  ✅  Exported  ({size_mb:.1f} MB)")

    # 6. Verify ONNX with onnxruntime
    print("\n  Verifying with onnxruntime...")
    import onnxruntime as ort
    sess = ort.InferenceSession(str(OUT_PATH), providers=["CPUExecutionProvider"])
    import numpy as np
    inp = dummy.numpy()
    out_ort = sess.run(None, {"input": inp})[0]
    assert out_ort.shape == (1, 512), f"ORT output shape wrong: {out_ort.shape}"

    # Compare PyTorch vs ORT output
    out_pt = out.numpy()
    max_diff = float(np.abs(out_pt - out_ort).max())
    print(f"  PyTorch vs ORT max difference: {max_diff:.2e}")
    assert max_diff < 1e-3, f"Large discrepancy: {max_diff}"
    print("  ✅  ORT output matches PyTorch (max_diff < 1e-3)")

    # 7. SHA-256
    import hashlib
    sha = hashlib.sha256(OUT_PATH.read_bytes()).hexdigest()
    print(f"\n  SHA-256: {sha}")
    print(f"  File:    {OUT_PATH.resolve()}")
    print(f"\n  Use this in the benchmark command:")
    print(f"    --model {OUT_PATH}  --sha256 {sha}")
    print()
    print("  REMINDER: local_models/ is in .gitignore — model will not be committed.")


if __name__ == "__main__":
    main()
