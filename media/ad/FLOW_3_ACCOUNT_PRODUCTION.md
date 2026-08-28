# EvidenceDesk — three-account Google Flow production pack

This plan produces three matching 20-second sections and a 60-second final ad. It uses only synthetic EvidenceDesk material and makes no claim of GovCloud authorization, compliance certification, enterprise-scale history, or an active paid subscription.

## Shared setup for all three accounts

Use a new Flow project on each account. Choose **Video → Veo 3.1 Lite → 16:9 → 8 seconds → one output**. Keep native audio enabled. Upload only the reference files listed for that section. Flow currently charges free users 10 credits for each Veo 3.1 Lite generation or extension, so the base plus two extensions costs 30 of the account's 50 daily credits and leaves 20 credits for retries. Credit costs can change; check Settings before generating.

Every section uses the same design language:

- clean warm-white environment, soft daylight, subtle paper texture;
- EvidenceDesk green `#11845A`, evidence orange `#E87524`, ink `#14251A`;
- exact uploaded EvidenceDesk logo; never invent or replace it;
- precise editorial typography, restrained motion, realistic product cinematography;
- 96 BPM minimal technology score in D minor: muted kick, soft sub pulse, glass clicks, paper movement, no vocals;
- no people, robots, holographic humanoids, fake dashboards, cyberpunk neon, generic blue AI brains, dialogue, narration, or singing;
- use the uploaded product screenshots as pixel-faithful product references: do not change their layout, wording, logo, colors, or controls;
- one short subtitle at a time, lower safe area, exact spelling, high contrast, no decorative extra text;
- each raw section is 24 seconds after two extensions. In Scenebuilder, trim the first 2 seconds and last 2 seconds to export one 20-second section.

Because AI-generated typography can still mutate, the authoritative captions are in `captions-v2.srt`. If any generated caption contains even one wrong letter, remove or cover it during final assembly and use the SRT instead.

## Account 1 — the hook and product reveal

Upload:

- `evidencedesk-logo-reference.png`
- `evidencedesk-about-light.png`

### Base generation prompt — 8 seconds

> Create the opening of a premium 16:9 product film for EvidenceDesk. Begin with two seconds of clean warm-white negative space with a faint paper grid and a low 96 BPM pulse. A dense organizational policy document then rushes toward camera; its words look orderly but the page fractures into hundreds of unsupported floating statements, creating a sharp visual hook. An orange scan line freezes the chaos. Show exactly one subtitle in the lower safe area: “Every answer can sound certain.” The subtitle must be spelled exactly. Modern editorial product cinematography, realistic paper physics, crisp daylight, deep ink, EvidenceDesk green and orange only. Minimal D-minor technology score, one paper impact and a glass verification click. No voice, no dialogue, no vocals, no people, no robots, no blue cyberpunk visuals, no extra text. End with the orange scan line travelling left to right across a clean white frame so the next extension can continue it.

### First Extend prompt — 8 seconds

> Continue seamlessly from the moving orange scan line. The unsupported statements stop and become slightly out of focus while one question remains sharp: “What is the policy?” Citation brackets close around it, then the exact uploaded EvidenceDesk logo resolves from the brackets without changing the logo. Show these subtitles sequentially, never together: “But can you prove it?” followed by “Meet EvidenceDesk.” Use a restrained push-in, one glass click when the brackets close, then let the same 96 BPM D-minor pulse gain a warm bass layer. No speech, no vocals, no extra wording. End with the exact logo centered on warm white as a narrow green line begins travelling rightward.

### Second Extend prompt — 8 seconds

> Continue seamlessly from the centered exact logo and travelling green line. The line becomes the top edge of the uploaded EvidenceDesk landing-page screenshot. Preserve the screenshot pixel-for-pixel and perform only a slow cinematic camera push with shallow parallax around the screen; do not redraw, rewrite, or invent any interface element. Reveal the promise through two sequential subtitles: “Ask your documents.” then “Inspect every supported claim.” Keep the same typography, lower safe area, green and orange accent system, and 96 BPM D-minor score. Add a quiet cursor tick and a soft verified chime. No speech or vocals. Finish on a clean white frame crossed by one orange line from left to right, with the logo small at center. This exact line-and-logo frame is the handoff to section two.

Export as `01-evidencedesk-hook.mp4`, trimming raw time `00:02–00:22`.

## Account 2 — real workflow and citation proof

Upload:

- `evidencedesk-logo-reference.png`
- `evidencedesk-library-light.png`
- `evidencedesk-cited-answer-light.png`

### Base generation prompt — 8 seconds

> Create section two of the same EvidenceDesk product film. Match a warm-white frame crossed by one orange line moving left to right, with the exact uploaded logo small at center. The line opens into the uploaded EvidenceDesk Library screenshot. Preserve the screenshot pixel-for-pixel—no changed text, controls, layout, or colors. Animate only a tasteful camera push and an abstract synthetic PDF card moving into the visible upload area. Sequential subtitle: “Upload approved documents.” Use the identical lower-safe-area typography from the previous section. Continue the same 96 BPM minimal D-minor technology score with muted kick, soft sub, paper whoosh, and no voice or vocals. End as an orange scanning beam begins moving down the abstract PDF card.

### First Extend prompt — 8 seconds

> Continue the orange scan moving down the synthetic PDF. The scan reveals clean text beneath a faint page image, visually communicating OCR without showing invented software panels. Small evidence fragments organize into a neat retrieval path leading toward the exact EvidenceDesk interface. Show these sequential subtitles: “OCR reads scanned PDFs.” then “Evidence is retrieved first.” Retain warm white, EvidenceDesk green `#11845A`, orange `#E87524`, deep ink, precise editorial motion, and the same 96 BPM D-minor score. Add a soft scanner pass and three restrained data clicks. No speech, no vocals, no fake statistics, no extra text. End as the evidence fragments converge into a single green verified point.

### Second Extend prompt — 8 seconds

> Continue from the single verified green point. It expands into the uploaded EvidenceDesk cited-answer screenshot. Preserve that screenshot pixel-for-pixel. The real question, grounded answer, citation buttons, and highlighted source should remain readable and unchanged. Use a slow left-to-right camera move that begins on the grounded answer and finishes on the highlighted source evidence. Show these exact sequential subtitles: “Answers stay grounded.” then “Citations open the source.” Add one quiet click as the citation opens and a warm verification chime. Maintain the same music without a new melody. No speech, vocals, extra wording, or invented UI. Finish on a clean warm-white frame crossed by one green line travelling left to right, exact logo small at center—the handoff to section three.

Export as `02-evidencedesk-workflow.mp4`, trimming raw time `00:02–00:22`.

## Account 3 — trust, use cases, and call to action

Upload:

- `evidencedesk-logo-reference.png`
- `evidencedesk-cited-answer-light.png`
- `evidencedesk-about-light.png`

### Base generation prompt — 8 seconds

> Create section three of the same EvidenceDesk product film. Begin with the matching warm-white handoff: one green line travelling left to right and the exact logo small at center. The line forms a secure boundary around several document cards; only authorized green cards pass through while restricted grey cards remain outside. No locks, shields, hacker imagery, or fake certification badges. Sequential subtitle: “Access is scoped before retrieval.” Keep the exact established lower-safe-area typography and the same 96 BPM D-minor minimal technology score. Add a soft boundary pulse, no voice or vocals. End with a question arriving at an empty evidence path.

### First Extend prompt — 8 seconds

> Continue from the question and empty evidence path. Instead of inventing an answer, the interface calmly pauses and displays an abstract honest refusal state: the empty path folds into an orange citation bracket and gently recedes. Do not fabricate a specific policy answer or fake feature screen. Show these exact sequential subtitles: “Weak evidence triggers a safe refusal.” then “Built for organizational knowledge.” Around the refusal, briefly arrange four simple paper labels—handbooks, procedures, operations, document review—using icons rather than extra rendered words if text accuracy is uncertain. Same warm-white visual system, green/orange accents, restrained animation, and continuous D-minor pulse. Add a soft descending tone followed by a reassuring verification click. No voice, vocals, alarm, or dramatic fear imagery. End as the orange citation bracket opens into white space.

### Second Extend prompt — 8 seconds

> Continue from the orange citation bracket opening into white space. Reveal the uploaded EvidenceDesk landing-page screenshot, preserved pixel-for-pixel, then gently pull back until the exact uploaded EvidenceDesk logo is centered above a minimal call-to-action plate. Show two sequential subtitles only: “Evidence before confidence.” then “Try the protected EvidenceDesk pilot.” The final frame must hold for at least three seconds: exact logo centered, “EVIDENCEDESK” and “PROTECTED PILOT” spelled exactly, warm-white background, one fine green line and one orange verification dot. Resolve the same 96 BPM score with a confident low note and a clean glass chime. No voice, vocals, payment claim, customer-count claim, certification badge, fake testimonial, or extra text.

Export as `03-evidencedesk-trust-cta.mp4`, trimming raw time `00:02–00:22`.

## Final assembly

Place the three exported clips in exactly this order with no transition effect:

1. `01-evidencedesk-hook.mp4`
2. `02-evidencedesk-workflow.mp4`
3. `03-evidencedesk-trust-cta.mp4`

Use a 6-frame audio crossfade at each join to hide small differences between the three generated music beds. Keep picture cuts hard because the matching line-and-logo handoffs provide the transition. Add the ElevenLabs narration from `narration-v2.txt`. Set narration around 145 words per minute, confident and warm rather than “trailer voice,” and duck the generated music by roughly 8–10 dB under speech. Import `captions-v2.srt` if any Flow-rendered caption is inaccurate. Export 1920×1080, H.264, 15–20 Mbps, AAC 48 kHz.

Official references:

- Google Flow creation and reference workflow: https://support.google.com/flow/answer/16353334
- Flow models and supported durations: https://support.google.com/flow/answer/16352836
- Current Flow credit costs: https://support.google.com/flow/answer/16526234
- Scenebuilder and trimming: https://support.google.com/labs/answer/16935718
