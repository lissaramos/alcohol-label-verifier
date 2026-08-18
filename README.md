# Label Verifier

An AI-powered prototype for verifying alcohol label artwork against the values submitted on a TTB label application — brand name, class/type, alcohol content, net contents, and the mandatory government warning statement.

Built as a standalone proof-of-concept, not integrated with COLA. Monorepo layout:

- `docs/` — Static frontend (plain HTML/CSS/JS, no build step). Served via GitHub Pages.
- `backend/` — Owns applications and verification results (SQLite + local image storage). Runs the field-matching/compliance logic. Calls the extraction service for OCR.
- `extraction-service/` — Small dedicated API that OCRs a label image and returns raw text. Currently Tesseract-based.

```
frontend (docs)  --->  backend (:8000)  --->  extraction-service (:9000)
                          |                       (Tesseract OCR)
                        SQLite + local image storage
```

## Approach

An agent enters what was submitted on the application (brand name, class/type, alcohol content, net contents) and uploads a photo of the label. The backend OCRs the label, then checks each field:

- **Brand name / class-type**: normalized (lowercased, punctuation stripped) and fuzzy-matched against the label text. An exact match after normalization → `match`. A close-but-imperfect match → `needs review`, surfaced to the agent rather than silently passed or failed — this is what makes "Stone's Throw" vs "STONE'S THROW" pass automatically while still flagging genuinely uncertain cases for a human.
- **Alcohol content**: the percentage is parsed out of both the submission and the OCR text and compared numerically, with a small tolerance band before something is flagged as a hard mismatch vs. a review.
- **Net contents**: volume + unit parsed and compared.
- **Government warning**: checked with an exact, case-sensitive substring match against the legally required text (27 CFR 16.21). Because OCR preserves case as printed, this alone catches "Government Warning" in title case or reworded text — no font-weight/bold detection needed. Per TTB requirements, this field has no tolerance: if it doesn't match exactly, the application fails, regardless of how well everything else matches.

An agent can override any non-matching field with a one-click "Mark match" / "Mark mismatch" — encoding the judgment call that fuzzy matching alone can't make, while leaving the government warning's exact-match requirement absolute (an override on it still recomputes correctly, but its own status still governs whether the application can ever show as fully verified without agent action).

Batch upload is a client-side queue: an agent adds several label+field entries before running verification, which then fires with a small concurrency cap (4 at a time) against the same single-item endpoint. This was a deliberate simplification over a server-side job queue — see Trade-offs below.

## Tools & technical choices

- **FastAPI + SQLite** for the backend — no real concurrency/scale requirements for a prototype, and SQLite keeps setup to zero external dependencies.
- **Tesseract (local OCR)**, not a cloud vision API. This directly follows two things from the interviews: the agency's firewall blocked the previous vendor's ML endpoints, and a 5-second response budget ruled out slow round-trips. Tesseract runs in-process with no network call and typically returns in well under a second on a normal label photo. The trade-off is accuracy on poorly-shot images — see below.
- **Vanilla HTML/CSS/JS frontend**, no framework or build step. Matches the "my 73-year-old mother could use it" requirement more directly than a heavier SPA would, and it's what lets the frontend be served as-is from GitHub Pages.
- **Two-service split** (backend vs. extraction-service) so the OCR/perception layer stays swappable — a future iteration could add a second extraction backend (e.g. a cloud vision model) behind the same `/extract` contract without touching the compliance logic.

## Assumptions & limitations

- **Scope**: verification covers the five fields called out in the prompt's sample label (brand name, class/type, alcohol content, net contents, government warning). Bottler name/address and country of origin are out of scope for this prototype.
- **Application data entry**: since the interviews explicitly said this prototype isn't integrating with COLA and didn't specify an intake format, applications are entered through a simple form (one per label) rather than assuming a particular upstream system.
- **Batch processing is client-orchestrated**, not a true async job queue. It comfortably handles tens of items with live per-row status; at the interview's cited scale (200-300 at once) a production version would want a server-side queue (e.g. Celery/RQ) so a dropped connection doesn't lose in-flight work. Documented here as the natural next step rather than built, given the prototype's time box.
- **ABV tolerance** is a simplification (a small fixed numeric band) rather than TTB's actual class-dependent legal tolerances — flagged as a known gap, not implemented.
- **Image quality**: basic preprocessing (grayscale, upscaling, contrast) helps Tesseract handle imperfect photos, but there's no deskew/perspective correction. Badly angled or heavily glared photos will still under-perform — exactly the case Jenny raised, and exactly where a cloud vision model would do better at the cost of the speed/network trade-off described above. A natural extension is a hybrid mode: local OCR first, with an optional (agent-triggered) cloud-assisted retry for label photos that fail extraction.
- **Security**: per the interview notes, no hardening was done beyond what's reasonable for a local prototype (no auth, no encryption at rest) — flagged explicitly since a production deployment would need it.

## Running everything locally

```bash
./dev.sh
```

This starts the backend (`:8000`), extraction service (`:9000`), and frontend (`:5500`) together. First time, install each service's dependencies (Tesseract must also be installed — `brew install tesseract` on macOS, `apt-get install tesseract-ocr` on Debian/Ubuntu):

```bash
cd backend && python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt && deactivate && cd ..
cd extraction-service && python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt && deactivate && cd ..
```

Or run services individually:

```bash
cd backend && uvicorn main:app --reload --port 8000
cd extraction-service && uvicorn main:app --reload --port 9000
cd docs && python3 -m http.server 5500
```

The frontend's "API base URL" field (top right) points at the backend — defaults to `http://localhost:8000` and remembers your choice.

The backend finds the extraction service via the `EXTRACTION_SERVICE_URL` env var, default `http://localhost:9000`.

## Deploying

- **Frontend**: In the repo's Settings → Pages, set Source to `Deploy from a branch`, branch `main`, folder `/docs`. Live at `https://<your-username>.github.io/alcohol-label-verifier/`.
- **Backend**: deploy anywhere that runs Python (Render, Railway, Fly.io, etc.). No system dependencies required.
- **Extraction service**: needs the Tesseract binary on the host, so it's set up to deploy via the included `extraction-service/Dockerfile` (installs `tesseract-ocr` via apt) rather than a plain Python buildpack. Point the backend's `EXTRACTION_SERVICE_URL` at wherever this ends up, and point the frontend's "API base URL" at the deployed backend.
