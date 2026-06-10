# DPIA — Biometrikus Arcfelismerés (LFA Practice Booking System)
## Data Protection Impact Assessment — GDPR Art. 35 sablon

> **Engineering/Product státusz:** ✅ ACCEPTED FOR ENGINEERING (Zoltán + ChatGPT szakmai review, 2026-06-10)
> **Legal/DPO státusz:** ⏳ FINAL SIGN-OFF PENDING BEFORE PRODUCTION ACTIVATION — not required for development phases
> **Production aktiválás:** ⛔ NOT PRODUCTION ACTIVE — `BIOMETRIC_FACE_MATCHING_ENABLED=false`
> **Verziószám:** v0.2-engineering-accepted
> **Utolsó módosítás:** 2026-06-10
> **Elkészítette:** Engineering team (technikai rész)
> **Megjegyzés:** Ez a dokumentum engineering/product szinten elfogadott fejlesztési referencia.
>   Külső DPO/jogtanácsosi aláírás NEM történt meg. Production aktiváláshoz külön legal/DPO
>   final sign-off szükséges (lásd PRODUCTION_ACTIVATION_CHECKLIST.md Gate 1).

---

## 1. Az adatkezelési művelet leírása

### 1.1 Adatkezelési cél

| Cél | Részletek |
|-----|-----------|
| **Elsődleges cél** | Akadémiai igazolvány-tulajdonos személyazonosságának biometrikus ellenőrzése beléptetéskor és digitális igazolványok kiadásakor |
| **Jogalap** | GDPR Art. 9(2)(a) — érintett explicit, visszavonható hozzájárulása |
| **Adatkezelő** | [Szervezet neve és székhelye] |
| **Adatfeldolgozók** | Self-hosted ONNX futtatókörnyezet (nincs harmadik fél cloud ML) — PR-5+ |

### 1.2 Érintetti kör

| Érintett | Részletek |
|----------|-----------|
| **Ki** | Az akadémiára regisztrált tanulók / játékosok |
| **Életkor** | Beleértve kiskorúakat (18 év alattiak) — szülői hozzájárulás szükséges |
| **Hozzájárulás mechanizmus** | Explicit opt-in: `POST /me/biometric-consent` (v1.0 szöveg) |
| **Visszavonás** | Bármikor: `DELETE /me/biometric-consent` — 30 napon belüli embedding törlés |

### 1.3 Kezelt biometrikus adatok

| Adatkategória | Tárolás módja | Hozzáférés |
|---------------|---------------|------------|
| **ArcFace embedding** (512-dim float32) | AES-256-GCM titkosítva, `user_face_embeddings` tábla | Nincs API — csak admin DB-hozzáférés |
| **Referencia fotó fájlnév** | Basename string, `biometric_verification_logs` | Admin audit |
| **Liveness challenge metadata** | High-level JSONB, `biometric_verification_logs` | Admin audit |
| **Hozzájárulás IP / User-Agent** | `user_biometric_consents` | Audit only |
| **face_match_score (scoring metadata)** | Float, `biometric_verification_logs.face_match_score` | Belső DB admin + threshold-tuning — **sosem API response, sosem logfájl** |

> **face_match_score adatvédelmi megjegyzés (PR-6, 2026-06-10):**
> A `face_match_score` (cosine hasonlósági érték, 0–1 skálán) biometrikus döntési scoring metaadat,
> amely a `biometric_verification_logs` táblában kerül tárolásra belső admin-review és
> threshold-kalibrációs célból. Ez az érték:
> - **Soha nem jelenik meg API response-ban** (strukturális Pydantic kényszer, tesztekkel igazolva)
> - **Soha nem kerül logfájlba** (log üzenetek csak `outcome` értéket tartalmaznak)
> - Production aktiválás előtt ezt az adatkategóriát **külön legal/DPO review** alá kell vonni,
>   és a felhasználói tájékoztató / DPIA végleges változatában dokumentálni kell.
> - A tárolás jogalapja az elsődleges biometrikus hozzájárulással azonos (GDPR Art. 9(2)(a)),
>   de a scoring metadata jellegéből adódóan a tájékoztatónak erre külön ki kell térnie.

**TILOS tárolni (enforced by sanitizer + schema):**
- Arclandmark koordináták (yaw, roll, pitch, landmarks)
- Eszközadat (device_model, ios_version)
- Raw frame / pixel adat
- Bounding box koordináták

---

## 2. Szükségesség és arányosság

### 2.1 Szükségességi teszt

| Kérdés | Válasz |
|--------|--------|
| Elérhető-e a cél kevésbé invazív módszerrel? | PIN / QR-kód egyenértékű lenne beléptetésnél — **értékelés szükséges** |
| Arányos-e az adatkezelés az érintetti körhöz? | Csak opt-in felhasználók; kiskorúaknál extra védelem szükséges |
| Alternatívák vizsgálata megtörtént? | [PENDING — DPO vizsgálja] |

### 2.2 Adatminimalizálás

- Embedding csak base64+AES tárolva, plaintext soha nem perzisztált
- Liveness metadata sanitizer (PR-1): 3 rétegű védelem (Swift struct, Pydantic schema, Python sanitizer)
- face_match_score API-ból kizárva (strukturális Pydantic kényszer)

---

## 3. Kockázatelemzés

### 3.1 Azonosított kockázatok

| # | Kockázat | Valószínűség | Hatás | Kockázati szint |
|---|----------|--------------|-------|-----------------|
| R1 | Embedding szivárgás API-n keresztül | Alacsony (schema guard) | Kritikus | **Közepes** |
| R2 | Liveness bypass (replay attack) | Közepes | Magas | **Magas** |
| R3 | DB kompromittálás (plaintext embedding) | Alacsony (AES-256-GCM) | Kritikus | **Közepes** |
| R4 | Kiskorú hozzájárulás szülő nélkül | Közepes (nem ellenőrzött) | Magas | **Magas** |
| R5 | Hozzájárulás-visszavonás utáni embedding maradék | Alacsony (30 nap törlés) | Magas | **Közepes** |
| R6 | Face match bias (bőrszín, nem) | Közepes (ArcFace buffalo_sc) | Magas | **Magas** |
| R7 | Admin-hozzáférés visszaélés (face_match_score) | Alacsony (RBAC) | Közepes | **Alacsony** |
| R8 | ONNX modell poisoning (supply chain) | Alacsony (checksum) | Kritikus | **Közepes** |

### 3.2 Mérséklő intézkedések

| Kockázat | Mérséklés | Státusz |
|----------|-----------|---------|
| R1 — API szivárgás | Pydantic schema struct. kizárás + teszt | ✅ PR-1/PR-2 kész |
| R2 — Replay attack | Challenge nonce + timestamp window | ⏳ PR-3 tervezi |
| R3 — DB breach | AES-256-GCM + IV per row | ⏳ PR-4 implementálja |
| R4 — Kiskorú | Szülői hozzájárulás flow | ⛔ PENDING — jogi döntés szükséges |
| R5 — Maradék adat | Celery delayed delete (30 nap) | ⏳ PR-4 implementálja |
| R6 — Bias | Modellvalidáció diverse test set-en | ⛔ PENDING — külső audit |
| R7 — Admin abuse | RBAC + audit log immutable | ✅ PR-1 kész |
| R8 — Model poisoning | SHA-256 checksum CI | ⏳ PR-5 tervezi |

---

## 4. Retention és törlési politika

| Adatkategória | Retention | Törlés módja |
|---------------|-----------|--------------|
| Hozzájárulás record | 5 év (bizonyítási kötelezettség) | Soft-delete; fizikai törlés 5 év után |
| Face embedding | Hozzájárulás visszavonásától 30 nap | Celery task (PR-4) |
| Biometrikus audit log | 5 év | Archiválás, nem törlés |
| face_match_score (scoring metadata) | 5 év (audit log soron belül) | Audit log sorral együtt — nem törlhető külön (sorban tárolt) |

---

## 5. Érintetti jogok

| Jog | Mechanizmus | Státusz |
|-----|-------------|---------|
| Hozzáférés | GET /me/biometric-consent | ✅ PR-2 |
| Törlés / visszavonás | DELETE /me/biometric-consent | ✅ PR-2 |
| Adathordozhatóság | [PENDING — export endpoint] | ⛔ Tervező |
| Tiltakozás | Hozzájárulás visszavonásával | ✅ PR-2 |
| Automatizált döntés elleni tiltakozás | Admin review fallback | ⏳ PR-7B |
| Előzetes tájékoztatás / disclosure | Consent modal (biometrikus tájékoztató) | ⏳ PR-7A |

---

## 6. Adatátvitel harmadik félnek

| Partner | Adat | Cél | GDPR alap |
|---------|------|-----|-----------|
| Nincs tervezett harmadik fél | — | Self-hosted ONNX | — |

> Ha cloud ML API-t (AWS Rekognition, Azure Face, Google Vision) vonnak be, új DPIA-fejezet szükséges.

---

## 7. Konzultációs kötelezettség

- [ ] DPO konzultáció — KÖTELEZŐ (GDPR Art. 35(2))
- [ ] Felügyeleti hatóság előzetes konzultáció — szükséges-e vizsgálandó (Art. 36)
- [ ] Kiskorúak szülői hozzájárulás jogi véleménye — KÖTELEZŐ
- [ ] Bias/fairness audit — KÖTELEZŐ production előtt

---

## 8. Jóváhagyási gate

> **A biometrikus feature (BIOMETRIC_FACE_MATCHING_ENABLED=true) produkción csak akkor engedélyezhető, ha:**
>
> - [ ] Ez a DPIA dokumentum jogi/DPO jóváhagyást kapott (aláírással)
> - [ ] Kiskorúak szülői hozzájárulás flow implementálva és jóváhagyva
> - [ ] Bias audit elvégezve és eredménye elfogadható
> - [ ] Embedding törlési Celery task (PR-4) deployed és tesztelt production-on
> - [ ] Adathordozhatósági export endpoint elkészült
> - [ ] Felügyeleti hatósági konzultáció lezárva (ha szükséges)

---

*Megjegyzés: Ez a dokumentum technikai vázlat. Jogi erővel bíró DPIA-vá csak DPO és jogtanácsos aláírásával válik.*
