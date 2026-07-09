# Gaelg AI — Website Tasks Brief

Working brief for bug fixes and feature additions to the **Gaelg AI** site
(<https://manx-ai.shef.ac.uk/>).

---

## Context for the agent (read first)

- The site is a **single-page app**: Speak, Listen, Translate, and About all render
  on the home route rather than as separate pages. This is the root cause of the
  broken back button (see Bug 3) and should be kept in mind throughout.
- It is served from a University of Sheffield server and exposed over HTTPS at
  `manx-ai.shef.ac.uk` using the institutional **Let's Encrypt** certificate.
- Backend provides text-to-speech, automatic speech recognition (ASR, 30s cap),
  and Manx ↔ English translation. The TTS box already shows a `0 / 500` character
  counter — reuse that pattern where the brief asks for one.
- The header already contains a **language flag toggle (EN / Manx)** that is
  currently disabled (see Feature 7).
- **Confirm the stack before changing things** (server framework, templating,
  JS routing, static asset layout). Several tasks reach beyond the repo into
  server/infra: the disk bug, the `gaelgai.im` cert, the Kaldi/Titan timestamper,
  and the VC experiment.

---

## Bug Fixes

### 1. Transcription error: `[Errno 28] No space left on device`
The ASR/transcription service intermittently fails with a "no space left on device"
error.

- **Root cause is unknown** — investigate where this is coming from. Likely
  candidates worth checking: temporary/intermediate WAV files not being cleaned up
  after each request, growing logs, or a model/cache directory filling the disk.
- **Goal:** find the source, then implement cleanup so the condition does not recur
  (e.g. remove temp files after each transcription).
- Graceful UI error handling is a nice-to-have, but fixing the underlying disk
  exhaustion is the priority.

### 2. Serve `gaelgai.im` over HTTPS
`gaelgai.im` (purchased via OVHcloud) already has DNS pointing to the Sheffield
server's IP, but is not currently available over HTTPS the way `manx-ai.shef.ac.uk`
is.

- Investigate how the existing cert is managed (the institutional cert uses Let's
  Encrypt; likely nginx/Apache + certbot) and add `gaelgai.im` (and `www.gaelgai.im`)
  so the site is reachable over HTTPS on that domain too.
- Add an HTTP → HTTPS redirect for the new domain.
- **Note:** ACME/Let's Encrypt issuance needs port 80 reachable for the domain; a
  firewall exemption for 80/443 may already exist but verify. Parts of this may be
  admin-side rather than code.
- **Decision to confirm:** should `gaelgai.im` serve the site directly as its own
  canonical domain, or 301-redirect to `manx-ai.shef.ac.uk`? (Recommend serving
  directly and treating `gaelgai.im` as the public-facing canonical.)

### 3. Browser back/forward button + real per-tab URLs — ✅ Done (commit 3e6bb43)
> **Hash-based routing** (`#speak` `#listen` `#translate` `#about`, home = no hash):
> back/forward via `hashchange`, refresh / shared links open the right tab, logo → home.
> Switched from History API pushState because pushState was throwing in the prod browser
> context (links worked but URL never changed). Hash routing is robust everywhere and needs
> **no nginx rewrite**. Trade-off: URLs are `…/#speak` rather than `/speak`. If clean paths
> are wanted later, we'd need to identify why pushState throws + add an nginx `try_files`
> fallback. New `/contribute` `/why` pages get added to `VALID_TABS`.

Because every tab lives on the home route, the browser back button doesn't move
between tabs.

- Use the **History API** so each tab switch pushes a history entry; back/forward
  should move between tabs as expected.
- Give each tab its **own URL**: `/speak`, `/listen`, `/translate`, plus `/about`,
  and the new `/contribute` and `/why` (Features 2–3). Deep links / refreshes should
  load directly into the correct tab.
- This routing also underpins the new pages and the Manx-language version (Feature 7).

### 4. Mobile-friendly home page — ✅ Done (commit 2668365)
- The three primary buttons (**Speak / Listen / Translate**) must **stack
  vertically** when the viewport narrows, instead of staying in a row.
- When the "about me" photo wraps **above** the text (narrow aspect ratios), it
  should be **centred**.
- Use sensible responsive breakpoints.

### 5. Listen page clarity + control consolidation — ✅ Done (commit 2668365)
- Make it clear in both the **record** and **upload** sections that the expected
  input is **Manx speech**.
- Collapse each set of controls to a **single button**: one record control that
  toggles **Start → Stop**, and one upload control. Remove redundant secondary
  labels (e.g. "Record audio" + "Start recording" on the same control; the duplicated
  "Upload a file" text).
- General principle: **less text is better.** No specific wording required.

### 6. Speak page — "Choose a voice" label — ✅ Done (commit 2668365)
- Add a **"Choose a voice"** heading/label prefacing the Female / Male selection.

### 7. Larger entry-text prompts — ✅ Done (commit 2668365)
- The text-entry prompts/placeholders (e.g. **"Enter Manx text"**, and the
  equivalents on the other tabs) need to be **much larger**.

### 8. Character counter on the Translate source box — ✅ Done (commit 2668365)
- Add a character-limit counter **below the translation source text box**, mirroring
  the existing TTS `0 / 500` counter.
- **Decision (resolved):** 500-character limit, same as TTS. Counter reads `0 / 500`
  and matches the backend check (`backend/main.py:989`).

### (Added) iOS / mobile recordings transcribed as nonsense — ✅ Done (commits ae2d232, 8084871)
> Priority bug raised mid-build. iPhone captured the mic far too quietly (~-25 dB, on one
> channel only) so Whisper hallucinated; laptop was fine. Fix: re-enable browser auto-gain
> (`{audio:true}`), per-platform mime detection (iOS records mp4/webm-opus, not always webm),
> and server-side `loudnorm` in the shared `normalise_audio` step — so it covers Manx + English
> ASR, recorded or uploaded, on any device.

---

## Feature Additions

### 1. Voice-conversion (VC) method comparison — offline experiment
Prioritise **all-out quality over speed**. Goal: determine which VC system best
**preserves quality/naturalness** when converting the existing TTS samples, judged by
**subjective** (Chris listening) and **objective** (MCD, F0, UTMOS) measures.

- **Type:** offline research experiment (runs on **Cassini**, not part of the web
  deployment). Self-contained, e.g. under `experiments/vc-comparison/`. The winning
  method will be wired into the Speak page later as a **separate** step.
- **Source samples to convert:**
  `acp24csb@cassini:/exp/exp1/acp24csb/model_instances/Grad-TTS_graphemic/out/manx`
  — objective results for the original wavs in this directory **already exist** and
  are the reference for measuring quality preservation.
- **VC systems to compare:** **kNN-VC**, **Vevo (timbre)**, and **Seed-VC**. Plan is
  to **clone each repo** and run inference over the samples.
- **Conditioning speakers:** pick one clear **male** and one clear **female** speaker
  from `acp24csb@cassini:/store/store1/data/HiFi-TTS` to condition the VC models on.
  A male and a female voice are wanted overall; targeting a single voice is fine for
  the experimentation phase.
- **Procedure:** for each VC method, run inference over the source samples to produce
  converted wavs; compute **MCD, F0 (RMSE), and UTMOS** on the outputs and compare
  against the originals' precomputed objective results.
- **Output:** converted wavs organised per method (for subjective listening) + a
  results table (CSV/markdown) written somewhere in the experiment dir.

### 2. "Contribute" page — ✅ Done (commit 8a1ad52) — folded into the About tab as a "Contribute" section (no separate page)
New page (`/contribute`, added to the nav) that:

- Explains the **need for contributions** (e.g. "these machines are only as good as
  the data we give them").
- Explains **how people can contribute** — proficient Manx speakers submitting data —
  and invites **feedback / suggested changes**.
- **No functional form for now:** use a **"get in touch"** call-to-action that links
  back to the contact section on the About page.

### 3. "Why" page — ✅ Done (commit 8a1ad52) — folded into About as "Why it matters" + "What's coming" sections, ending with a link to the Contribute section
New page (`/why`, added to the nav) that:

- Explains **why using this technology matters** and what these tools could be used
  for — **language preservation, assistive technologies, education**.
- Includes a **"What's coming / Future directions"** section.
- **Ends with a link to the Contribute page.**

### 4. Publications section on About — ✅ Done (commit 1040d6f) — renders from frontend/static/publications.bib; Chris adds entries there
- Add a **Publications** section to the About page.
- Render the list **from a BibTeX file** (e.g. `publications.bib`) — **Chris will
  populate the `.bib` entries manually**. The agent builds the parsing/rendering
  (formatted citations, with DOI/links where present) and leaves the `.bib` for Chris
  to fill in.

### 5. "Timestamper" — speech/text alignment (Kaldi via Titan)
A forced-alignment tool for audio up to **30 minutes**.

- Kaldi already exists on **Titan**, but a **clean end-to-end alignment script is not
  yet set up**. Because Kaldi is complex to build, the website should **send
  alignment requests to Titan** rather than running Kaldi locally. This is fine as the
  tool is **CPU-only**.
- Work involved: (a) set up a clean end-to-end alignment script on Titan; (b) add a
  Timestamper tab/page on the website that submits jobs to it.
- Because audio can be up to 30 minutes and runs CPU-only, treat this as an
  **asynchronous/job-based** request rather than a blocking call.
- **Decision to confirm (assumed, flagged):**
  - **Input** — assumed **audio + a transcript** (forced alignment requires the text
    to align). Confirm whether the user supplies the transcript, or whether audio-only
    is expected (which would need an ASR pass first).
  - **Output format** — assumed **word/segment-level timestamps** shown in an on-page
    aligned view, plus a **downloadable SRT/VTT and/or TextGrid**. Confirm preferred
    format(s).

### 6. English transcription option on the Translate page — ✅ Done (commits 17ce8f6, f1bce75) — /transcribe_en (clean whisper-large-v3, asr_en); source-box mic routes by language after ⇄ swap; verified on prod
- Add a **microphone button in the English box** of the Translate page, wired to an
  **English ASR (Whisper)** so users can speak English to populate the English field
  (then translate to Manx).
- **No English endpoint exists yet** — stand one up (should be straightforward; pick
  an appropriate Whisper model size given compute constraints).
- Mirror the Manx ASR's **30-second** cap unless there's reason to differ.

### 7. Manx-language version of the site (i18n scaffolding) — ✅ Done (commit b8a5bcf) — English fallback guaranteed; old MT cleared (empty T.gv); flag toggle re-enabled. Chris adds verified Manx to T.gv. Body prose can be keyed incrementally.
Professional translations will be supplied later; build the **infrastructure** now.

- **Externalise all UI strings** into a locale system with **English as the source of
  truth**.
- Re-enable the existing header **flag toggle** (currently disabled because the old
  machine translations were wrong) and wire it to the i18n system.
- **Critical:** users must **never see incorrect machine-translated text**. Any
  untranslated Manx key must **fall back to the English string** — do **not** ship
  visible MT placeholders. Chris will drop in professional translations once content
  is finalised; only then should real Manx copy appear.

### 8. Collapsible nav (hamburger menu) — ✅ Done (commit 3838e35)
- The navigation bar should **collapse into a hamburger menu** at narrow widths /
  when the aspect ratio requires it. Standard responsive behaviour.

---

## Open decisions to confirm
1. **Timestamper I/O** — input (audio + transcript vs audio-only) and output format
   (SRT / VTT / TextGrid / on-page view). *(Feature 5)*
2. **`gaelgai.im` canonical** — serve directly as canonical, or redirect to
   `manx-ai.shef.ac.uk`? *(Bug 2)*
3. ~~**Translate character limit** — reuse 500, or a different value? *(Bug 8)*~~
   → **Resolved:** 500, same as TTS.
