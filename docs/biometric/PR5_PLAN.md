# PR-5 — Model-agnostic ONNX Embedding Provider (R&D/Prototype)
## feat/biometric-pr5-onnx-provider

> **Engineering/Product státusz:** ACCEPTED FOR ENGINEERING (Zoltán + ChatGPT, 2026-06-10)
> **R&D / Prototype use:** ALLOWED FOR INTERNAL EVALUATION
> **Production use:** NOT ACCEPTABLE without separate model license + legal/DPO gate
> **`BIOMETRIC_FACE_MATCHING_ENABLED`:** unchanged = false
> **`BIOMETRIC_ONNX_RND_ENABLED`:** false (default) — R&D-only guard, never true in production
> **Nem KYC. Nem production-ready.**

---

## R&D modell referencia

| Modell | R&D státusz | Production státusz | Megjegyzés |
|--------|-------------|-------------------|------------|
| ArcFace ONNX (onnxmodelzoo/arcfaceresnet100-8) | ✅ ALLOWED FOR INTERNAL EVALUATION | ⛔ NOT ACCEPTABLE | Apache 2.0 weight, de MS-Celeb-1M training (non-commercial) |
| AuraFace v1 (fal/AuraFace-v1) | ✅ ALLOWED FOR INTERNAL EVALUATION | ⚠️ ACCEPTABLE WITH LEGAL REVIEW (pending) | Commercial-friendly intent; training dataset dokumentáció még PENDING |

**Model weight commit policy:** *.onnx fájlok .gitignore-ban. Soha nem kerül a repóba.
Model path: `BIOMETRIC_ONNX_MODEL_PATH` env var → filesystem path only, nem URL/CDN.

---

## Aktiválási guardok

```
BIOMETRIC_EMBEDDING_PROVIDER=fake    (default — mindig)
BIOMETRIC_ONNX_RND_ENABLED=false     (default — soha ne legyen true production-ban)
BIOMETRIC_FACE_MATCHING_ENABLED=false (unchanged)

ONNX provider indításához MINDKETTŐ kell:
  BIOMETRIC_ONNX_RND_ENABLED=true  AND  BIOMETRIC_FACE_MATCHING_ENABLED=true
```

---

## Out-of-scope (PR-5-ben tilos)

cosine similarity threshold · face_match_status="verified" · production activation ·
iOS · admin review UI · face_match_score response · bias/fairness lezárása ·
AuraFace production döntés · key rotation · model weight commit to GitHub