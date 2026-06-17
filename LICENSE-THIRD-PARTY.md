# Third-Party Licences

## ML Models

### SSD MobileNet v1 COCO (ONNX)
- Source: ONNX Model Zoo / Hugging Face
  https://huggingface.co/onnxmodelzoo/ssd_mobilenet_v1_12
- Original: TensorFlow Model Zoo (Google)
  https://github.com/tensorflow/models
- Licence: Apache-2.0
- Copyright: Copyright Google LLC
- Training data: MS COCO 2017 (annotations: CC-BY 4.0)

## Python Dependencies

### ONNX Runtime
- Source: https://pypi.org/project/onnxruntime/
- Licence: MIT
- Copyright: Copyright (c) Microsoft Corporation

### OpenCV (opencv-python-headless)
- Source: https://pypi.org/project/opencv-python-headless/
- Licence: Apache-2.0 (OpenCV library) + MIT (Python wrapper)
- Copyright: Copyright (C) 2000-2024, Intel Corporation; Willow Garage Inc.
- Note: Wheels ship with FFmpeg (LGPLv2.1) and other third-party
  libraries. See "opencv-python-headless Wheel Third-Party Dependencies"
  section below for details.

### NumPy
- Source: https://pypi.org/project/numpy/
- Licence: BSD-3-Clause
- Copyright: Copyright (c) 2005-2024, NumPy Developers

### Pillow
- Source: https://pypi.org/project/pillow/
- Licence: MIT-CMU (HPND variant)
- Copyright: Copyright (c) 1997-2011 by Secret Labs AB;
  Copyright (c) 1995-2011 by Fredrik Lundh and contributors

## Datasets

### MS COCO
- Source: https://cocodataset.org/
- Annotations licence: CC-BY 4.0
- Attribution: Microsoft COCO: Common Objects in Context.
  Lin et al., 2014. https://arxiv.org/abs/1405.0312
- Note: Only model weights (trained on COCO) are used.
  No COCO images are stored, distributed, or displayed.

## opencv-python-headless Wheel Third-Party Dependencies

The opencv-python-headless binary wheel bundles compiled third-party
libraries under their respective licences. The authoritative list is
maintained by the opencv-python project at:
https://github.com/opencv/opencv-python/blob/master/LICENSE-3RD-PARTY.txt

Key bundled libraries and their licences:

| Library | Licence |
|---|---|
| FFmpeg | LGPLv2.1 (dynamic linking, not modified) |
| libjpeg-turbo | IJG licence + BSD-3-Clause |
| libpng | libpng licence (BSD-like) |
| zlib | zlib licence (BSD-like) |
| libtiff | BSD-like |
| OpenEXR | BSD-3-Clause |

These are dynamically linked binary dependencies inside the wheel.
Our source code does not modify, recompile, or redistribute them
separately. They ship as part of the pre-built opencv-python-headless
PyPI wheel.

The FFmpeg inclusion uses LGPLv2.1 dynamic linking, which does not
impose copyleft obligations on the calling application. The
opencv-python-headless variant specifically excludes GUI dependencies
(Qt, GTK) to minimise the third-party licence surface.
