# Juggling Detection — Licence and No-Paid-Model Policy

**Status: FINAL — non-negotiable. Applies to every phase of the detection roadmap.**

**Recorded: 2026-06-12**

---

## Core Principle

> The entire detection roadmap is built on our own labeled dataset and our own
> trained models, using exclusively free, permissively licensed tools.
> Paid models, paid licences, and paid inference APIs are not an option.

---

## What Is NOT Allowed

| Category | Prohibited |
|---|---|
| **Paid models** | Any commercial model requiring a purchase or subscription |
| **Paid inference API** | Azure Computer Vision, Google Vision AI, AWS Rekognition, Roboflow hosted inference, any per-call billing |
| **AGPL production dependency** | Any AGPL-3.0 licensed software used in the production backend or iOS app (triggers mandatory source publication for SaaS) |
| **Commercial YOLO / Ultralytics licence** | The free Ultralytics tier is AGPL-3.0; the commercial tier is paid — both are prohibited |
| **Vendor lock-in** | Any solution that ties production functionality to a single paid platform |
| **Conditionally free models** | Models that are "free for research" but require a paid licence for commercial / SaaS use |
| **Unaudited licences** | Any model or library whose licence has not been explicitly verified |

---

## What IS Allowed (Audited)

| Component | Licence | Verified | Notes |
|---|---|---|---|
| **OpenCV** (`opencv-python-headless`) | Apache 2.0 | ✓ | Frame processing, baseline detection |
| **MediaPipe Pose** | Apache 2.0 | ✓ | Body pose landmarks; offline, no API |
| **PyTorch** | BSD-3-Clause | ✓ | Training framework |
| **torchvision** | BSD-3-Clause | ✓ | SSD / RetinaNet training backbones |
| **ONNX** | Apache 2.0 | ✓ | Model export format |
| **onnxruntime** | MIT | ✓ | Inference runtime (already in project) |
| **LabelImg** | MIT | ✓ | Bounding box annotation tool |
| **ffmpeg** | LGPL-2.1 | ✓ | Frame extraction (already in project) |
| **Our own trained model** | Proprietary | — | Built on the above stack |

---

## Grey Zone — Requires Licence Audit Before Use

| Component | Issue | Decision |
|---|---|---|
| **FootAndBall** | Not on PyPI; GitHub repo licence unverified as of 2026-06-12; PyTorch dependency (BSD ✓) but repo's own code licence is unclear | **Blocked until explicit licence audit**. May be referenced in benchmarks but must not be a production dependency until cleared. |
| **YOLO / Ultralytics** | AGPL-3.0 | **Permanently blocked for production use.** May be run offline on a dev machine for benchmark reference only; must never be imported into the backend codebase or iOS app. |

---

## How to Add a New Dependency

Before adding any new model, library, or tool to the detection pipeline:

1. Check the licence on PyPI / GitHub
2. Verify it is one of: MIT, Apache 2.0, BSD-2-Clause, BSD-3-Clause, LGPL (with care)
3. Confirm it does not have a "non-commercial only" or "research only" clause
4. Record the finding in this file or in the relevant PR description
5. If uncertain, **do not use it** — ask first

---

## Rationale

LFA's juggling scoring system is a commercial SaaS product. Using AGPL software
in the backend would legally require publishing the entire backend source code.
Using paid models introduces recurring cost and vendor dependency that conflicts
with the project's self-reliance goals. The permissive-licence stack above
(Apache 2.0 / BSD / MIT) is commercially safe, portable, and under our full control.
