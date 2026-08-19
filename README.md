# Label Verifier

An AI-powered prototype for verifying alcohol label artwork against the values submitted on a TTB label application — brand name, class/type, alcohol content, net contents, and the mandatory government warning statement. Verification rules adapt to the beverage category (distilled spirits, wine, or beer/malt beverage), since TTB's requirements genuinely differ between them.

Built as a standalone proof-of-concept, not integrated with COLA. Monorepo layout:

- `docs/` — Static frontend (plain HTML/CSS/JS, no build step). Served via GitHub Pages.
- `backend/` — Owns applications and verification results (SQLite + local image storage). Runs the field-matching/compliance logic. Calls the extraction service for OCR.
- `extraction-service/` — Small dedicated API that OCRs a label image and returns raw text. Runs PaddleOCR (PP-OCRv5).

```
frontend (docs)  --->  backend (:8000)  --->  extraction-service (:9000)
                          |                       (PaddleOCR / PP-OCRv5)
                        SQLite + local image storage
```

## Approach

An agent selects a beverage type, enters what was submitted on the application (brand name, class/type, alcohol content, net contents), and uploads a photo of the label. The backend OCRs the label, then checks each field:

- **Brand name / class-type**: normalized (lowercased, punctuation stripped) and fuzzy-matched against the label text. An exact match after normalization → `match`. A close-but-imperfect match → `needs review`, surfaced to the agent rather than silently passed or failed — this is what makes "Stone's Throw" vs "STONE'S THROW" pass automatically while still flagging genuinely uncertain cases for a human. Because this searches for the expected value *anywhere* in the OCR text rather than assuming a fixed layout, it's naturally tolerant of **personalized labels** — extra custom text (a gift message, an event name) on the label doesn't interfere with matching the mandatory fields, since nothing requires the label to contain *only* those fields. Verified directly with a synthetic personalized label during development.
- **Alcohol content**: category-dependent, since this is where TTB's rules diverge most:
  - *Distilled spirits*: always mandatory, matched against the OCR'd percentage with a tight tolerance.
  - *Wine*: mandatory, but 27 CFR 4.36 grants a wider legal tolerance (±1.5 points under 14% ABV, ±1.0 at/above it — fortified wines). Wines in the 7–14% band may also state "Table Wine" or "Light Wine" instead of a percentage; the check accepts either.
  - *Beer*: TTB does **not** federally require an ABV statement on malt beverage labels. If the agent leaves the field blank, it's marked `not applicable` rather than flagged as missing. If a value is provided (common practice, sometimes state-mandated), it's still verified.
- **Net contents**: volume + unit parsed and compared, unit-normalized so "12 FL OZ", "fl. oz.", and "floz" all match — the standard beer unit, which differs from the mL/L convention on wine and spirits.
- **Government warning**: checked with an exact, case-sensitive substring match against the legally required text (27 CFR 16.21). Because OCR preserves case as printed, this alone catches "Government Warning" in title case or reworded text — no font-weight/bold detection needed. This applies identically to all three categories and has no tolerance: if it doesn't match exactly, the application fails regardless of how well everything else matches.
- **Sulfite declaration** (wine only): wine with ≥10ppm sulfur dioxide must carry a "Contains Sulfites" statement (27 CFR 4.32(e)). Since the label alone can't confirm whether the product actually contains that much, a missing statement is surfaced for agent review rather than an automatic fail.

An agent can override any non-matching field with a one-click "Mark match" / "Mark mismatch" — encoding the judgment call that fuzzy matching alone can't make, while leaving the government warning's exact-match requirement absolute (an override on it still recomputes correctly, but its own status still governs whether the application can ever show as fully verified without agent action). Fields marked `not applicable` (e.g. blank beer ABV) aren't override candidates — there's nothing to judge.

Batch upload is a client-side queue: an agent adds several label+field entries before running verification, which then fires against the same single-item endpoint (currently one at a time — see the OCR engine benchmark below for why). This was a deliberate simplification over a server-side job queue — see Trade-offs below.

## Tools & technical choices

- **FastAPI + SQLite** for the backend — no real concurrency/scale requirements for a prototype, and SQLite keeps setup to zero external dependencies.
- **PaddleOCR / PP-OCRv5 (local OCR)**, not a cloud vision API — still satisfies the interviews' firewall constraint (no outbound calls at request time; see the Dockerfile note below on baking model weights in at build time instead) while handling the stylized fonts and decorative layouts real labels use far better than a classical OCR engine. See the benchmark writeup just below — this replaced an initial Tesseract-based implementation after side-by-side testing showed it silently missing fields on real label photos.
- **Vanilla HTML/CSS/JS frontend**, no framework or build step. Matches the "my 73-year-old mother could use it" requirement more directly than a heavier SPA would, and it's what lets the frontend be served as-is from GitHub Pages.
- **Two-service split** (backend vs. extraction-service) so the OCR/perception layer stays swappable — this is what let the engine get replaced without touching any compliance/matching logic, just the `/extract` implementation behind it.

## OCR engine: Tesseract → PaddleOCR (PP-OCRv5)

The prototype originally used Tesseract. Testing against a real label photo (a photographed spirits label, front and back) surfaced a real problem: Tesseract silently missed the alcohol content and net contents entirely — not misread, just absent from the output — apparently due to the label's stylized bold serif brand typography. Both fields required a manual agent override every time.

Benchmarked PP-OCRv5 against the same photo before committing to the swap:

| | Tesseract | PP-OCRv5 (mobile det+rec) |
|---|---|---|
| Brand / class-type / warning | ✅ read | ✅ read |
| Alcohol content ("45% ALC/VOL") | ❌ missing entirely | ✅ read correctly |
| Net contents ("750 ML") | ❌ missing entirely | ✅ read correctly |
| Same label, rotated 8° | Output mostly unreadable garbage | Still substantially correct — a few individual character swaps, all key fields intact |
| Single-image latency | ~0.3s | ~2.1–2.8s |

PP-OCRv5 also directly helps with **personalized labels** and **multi-photo submissions**: since the matching logic searches for expected values anywhere in the OCR text rather than assuming a fixed layout (see Approach above), better raw text extraction on artistic/decorative labels flows straight through to better field matching, with no changes needed elsewhere. A follow-up test cropping the label into separate front/back photos (closer to how these are actually submitted) confirmed both extract cleanly on their own.

**The real cost is concurrency, not per-request speed.** A single extraction easily clears the 5-second budget. But PP-OCRv5 is CPU-bound and doesn't parallelize well on this hardware: 2 concurrent extractions already pushed per-request latency to ~5s, and 4 concurrent pushed it to ~10s — confirmed to be genuine compute contention (isolated with direct Python threading, and tried capping Paddle's internal thread count, neither changed the picture). Tesseract's classical algorithm had none of this problem. The mitigation here is blunt: the batch queue now processes one item at a time instead of four, trading slower aggregate batch throughput for keeping a single agent's wait time close to the clean single-request latency. **This was benchmarked on a 16-core dev machine — a small cloud instance will have far less headroom, and single-request latency itself should be re-measured on the actual deployment target before trusting the 5-second budget in production.** A more complete fix (worker pool sized to available cores, a dedicated inference server, or GPU acceleration) is future work, not implemented here.

**Two more issues only showed up in production, on real x86_64 hardware:**
1. Paddle's oneDNN (Intel CPU) backend defaults on and crashed the first real request with `NotImplementedError: ConvertPirAttribute2RuntimeAttribute not support [...DoubleAttribute]` — a bug in this Paddle version's oneDNN path. Invisible in all local testing since oneDNN is Intel-specific and never activates on Apple Silicon. Fixed with `enable_mkldnn=False`.
2. Even with that fixed, the service still OOMed on Render's 512MB tier. Local memory profiling (`psutil`, measuring RSS around the actual `_ocr.predict()` call) found the real number: model load alone takes ~850MB, and a real 12MP phone photo (4032×3024) sent through uncapped pushed peak usage to **6.2GB** — the image-size caps added earlier (`MIN_DIMENSION`) only handled small/cropped photos, never bounded large ones. Added a matching `MAX_DIMENSION=1600` downscale cap, which brought the same photo down to ~1.7GB with no visible accuracy loss. **A 512MB instance cannot run this service; budget at least ~2GB.**

## Assumptions & limitations

- **Scope**: verification covers the five fields called out in the prompt's sample label (brand name, class/type, alcohol content, net contents, government warning). Bottler name/address and country of origin are out of scope for this prototype.
- **Application data entry**: since the interviews explicitly said this prototype isn't integrating with COLA and didn't specify an intake format, applications are entered through a simple form (one per label) rather than assuming a particular upstream system.
- **Batch processing is client-orchestrated**, not a true async job queue, and (per the OCR benchmark above) currently processes one item at a time rather than in parallel. It has live per-row status and handles tens of items fine, but at the interview's cited scale (200-300 at once) a production version would want a server-side job queue with worker concurrency actually sized to the deployment host, so a dropped connection doesn't lose in-flight work and throughput isn't limited to one label at a time.
- **Category-specific rules** cover the differences most relevant to verification — ABV requirements/tolerances and the wine sulfite declaration. Other category-specific label elements TTB requires (e.g. appellation of origin on wine, specific class/type subcategories for spirits) are out of scope for this prototype, same as bottler address/country of origin above.
- **Wine ABV tolerance** uses a simplified two-band model (±1.5 under 14%, ±1.0 at/above) approximating 27 CFR 4.36 rather than every class-specific exception in the full regulation.
- **Image quality**: images are upscaled before OCR when small (this measurably fixed a cropped-photo test case during benchmarking), and PP-OCRv5's textline-orientation classifier handles moderate rotation well. There's still no perspective/deskew correction for photos taken at a sharp angle, and heavy glare will still hurt extraction — exactly the case Jenny raised. A natural extension is a hybrid mode: PP-OCRv5 first, with an optional (agent-triggered) cloud-vision retry for label photos that still fail extraction.
- **Security**: per the interview notes, no hardening was done beyond what's reasonable for a local prototype (no auth, no encryption at rest) — flagged explicitly since a production deployment would need it.

## Running everything locally

```bash
./dev.sh
```

This starts the backend (`:8000`), extraction service (`:9000`), and frontend (`:5500`) together. First time, install each service's dependencies:

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

The extraction service's first startup downloads the PP-OCRv5 model weights (~30s, one-time, cached to disk afterward) — the first request will be slow while this happens if you don't wait for it to finish first.

## Deploying

- **Frontend**: In the repo's Settings → Pages, set Source to `Deploy from a branch`, branch `main`, folder `/docs`. Live at `https://<your-username>.github.io/alcohol-label-verifier/`.
- **Backend**: deploy anywhere that runs Python (Render, Railway, Fly.io, etc.). No system dependencies required.
- **Extraction service**: deploy via the included `extraction-service/Dockerfile` rather than a plain Python buildpack — it bakes the PP-OCRv5 model weights into the image at build time (`RUN python -c "import main"`), so the running container never needs outbound internet access, matching the firewall constraint from the interviews. Building the image does need internet access. Point the backend's `EXTRACTION_SERVICE_URL` at wherever this ends up, and point the frontend's "API base URL" at the deployed backend. **Needs at least ~2GB RAM** (confirmed by an actual OOM crash on a 512MB tier — see the OCR section above for the profiling numbers behind that). **Before relying on this in front of real users, re-run the concurrency benchmark from the OCR section above on the actual host** — it was measured on a 16-core dev machine, and a small cloud instance will hit the CPU contention ceiling sooner.
