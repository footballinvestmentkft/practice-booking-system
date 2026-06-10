# Biometric Feature — Production Activation Checklist
## BIOMETRIC_FACE_MATCHING_ENABLED: false → true

> **Engineering/Product státusz:** ✅ ACCEPTED FOR ENGINEERING (Zoltán + ChatGPT szakmai review, 2026-06-10)
> **Legal/DPO státusz:** ⏳ FINAL APPROVAL PENDING BEFORE PRODUCTION — not required for development phases
> **Production aktiválás:** ⛔ NOT PRODUCTION ACTIVE — `BIOMETRIC_FACE_MATCHING_ENABLED=false` locked
> **Utolsó felülvizsgálat:** 2026-06-10
> Ez a dokumentum az egyetlen jóváhagyott útvonal a feature production engedélyezéshez.
> Minden sor letickelhető állapotban kell legyen, ÉS írásos approval gate szükséges (Gate 1 legal/DPO).

---

## Gate 1 — Jogi / Adatvédelmi jóváhagyások (BLOCKER)

- [ ] **DPIA elfogadva** — DPO aláírta (lásd `docs/biometric/DPIA_TEMPLATE.md`)
- [ ] **Jogtanácsosi jóváhagyás** — adatkezelési cél, jogalap, retention
- [ ] **Kiskorúak szülői hozzájárulás** — jogi vélemény + technikai flow kész
- [ ] **Felügyeleti hatósági konzultáció** — elvégezve vagy megállapítva, hogy nem szükséges
- [ ] **Bias/fairness audit** — external auditor, diverse test set eredménye elfogadható
- [ ] **Adatfeldolgozói szerződések** — ha bármilyen third-party bevonásra kerül

**Approval gate:** `[Dátum] [DPO neve] [Jogtanácsos neve] [Aláírás/ticketing ref]`

---

## Gate 2 — Technikai készültség (BLOCKER)

### PR-sorozat teljes merge-e main-be:
- [x] PR-1 — Biometric foundation (model, audit log, sanitizer, feature flag)
- [x] PR-2 — Consent API (POST/GET/DELETE /me/biometric-consent)
- [ ] PR-3 — Liveness reference flow (onboarding_liveness_capture)
- [ ] PR-4 — Embedding generálás (AES-256-GCM, Celery delayed delete)
- [ ] PR-5 — ONNX / InsightFace (self-hosted, checksum, CI mock)
- [ ] PR-6 — Face matching (threshold, review band, match/fail flow)
- [ ] PR-7 — Admin review UI (manual review queue)
- [ ] PR-8 — Monitoring / alerting / rate limiting

### Adathordozhatóság:
- [ ] `GET /me/biometric-data-export` endpoint elkészült és tesztelt

### Embedding törlés:
- [ ] Celery `biometric_embedding_delete` task deployed production-on
- [ ] 30 napos retention tesztje elvégezve
- [ ] Dead-letter queue és retry konfiguráció dokumentált

### ONNX modell:
- [ ] `insightface_buffalo_sc_v1` SHA-256 checksum rögzítve és CI-ban ellenőrzött
- [ ] Modell nem cloud-letöltéssel, hanem artifact registry-ből deploy-olva
- [ ] Fallback viselkedés tesztelt (modell nem elérhető esetén)

---

## Gate 3 — Security audit (BLOCKER)

- [ ] Penetration test elvégezve a biometric API endpoint-okon
- [ ] Replay attack védelem tesztelt (liveness nonce + timestamp)
- [ ] API rate limiting biometric endpoint-okra beállítva és tesztelt
- [ ] AES kulcsrotáció eljárás dokumentált és tesztelt
- [ ] `face_match_score` API-ból való kizárása pen-test-tel megerősítve

---

## Gate 4 — Operacionális készültség

- [ ] Runbook megírva: `docs/biometric/RUNBOOK.md`
  - consent revocation eljárás
  - embedding deletion eljárás
  - GDPR Art. 17 törlési kérelem eljárás
  - incident response biometric breach esetén
- [ ] Monitoring dashboard biometric event-ekre (Grafana / equivalent)
- [ ] Alert: `consent_revoked` után 30 napon belül embedding törlés sikertelen
- [ ] Oncall eljárás biometric-specifikus incidensekre
- [ ] Adatbázisos backup biometric táblákon tesztelt és dokumentált

---

## Gate 5 — Staged rollout terv

- [ ] Feature flag environment-specifikus értékek dokumentálva:
  - `development`: true (fejlesztés)
  - `staging`: true (QA + pen-test)
  - `production`: **false** (jelen állapot) → true csak minden gate után
- [ ] Canary rollout terv (pl. 5% → 25% → 100%)
- [ ] Rollback eljárás dokumentálva (`BIOMETRIC_FACE_MATCHING_ENABLED=false` visszaállítás hatásai)

---

## Írásos Approval Gate

> **A production feature flag true értékre állításához a következő személyek írásos jóváhagyása szükséges:**
>
> | Szerepkör | Név | Dátum | Ref |
> |-----------|-----|-------|-----|
> | Engineering Lead | | | |
> | DPO | | | |
> | Jogtanácsos | | | |
> | Termékfelelős (Product Owner) | | | |
>
> Jóváhagyás módja: ticketing rendszer (Jira/Linear) + email visszaigazolás.
> A jóváhagyás érvényessége 90 nap. Utána ismételt review szükséges.
