# EmbedProof launch video: brag-plan

Input type: Project (`H:\IEEE_CLimate_hack`). Style: Glass Layers (brag-styles set `glass-layers`, decided by the brief). Tone: `polished` pacing with `default` punch. Narration off (`--voice` would add it). 1920x1080, 30 fps, 20.5 s, code-made music and sfx (motion-kit soundkit, `pulse` bed at 120 BPM, so every cue lands on a half-second grid).

## The brag questions

- **What is it?** EmbedProof turns a steel mill's CO₂-per-tonne claim into a tamper-evident, quantum-safe certificate: an AI plausibility model checks the claim, ECDSA and ML-DSA-44 (FIPS 204) sign it, and a smart contract refuses to hand the same verified tonne to two buyers.
- **Who is it for?** EU steel importers, Turkish exporters, and the IEEE ClimateChain judges (blockchain security and post-quantum).
- **What sets it apart?** Double-claiming treated as double-spending (`DoubleClaimBlocked(1, 5000, 0)`), an AI gate that changes on-chain state, and a public verify page where one edited digit breaks both signatures.
- **Boldest claims, all from the repo and re-measured on my own stack:** honest EAF mill scores risk 20 (green); a BF-BOF mill declaring 0.56 tCO₂/t scores risk 100 (red) at "75% below the model median 2.28"; one edited digit fails ECDSA, ML-DSA-44 and the fingerprint check; 178 contract tests at 100% coverage (README and docs/DEVPOST.md).
- **Visual hook:** a frosted panel over drifting colour carrying the PRD's own line, "Nobody can see the CO₂ in a steel coil."
- **Share caption:** `share-copy.txt`.

## Angle, hook, highlights, punchline

- **Angle:** nobody can see the CO₂ in a steel coil, so make the claim checkable and the tonne unsellable twice.
- **Hook (0 to 3 s):** the line in two weights; a "Declared 0.56 tCO₂ / t" chip lands on the panel as a tease that pays off in the next scene (BF-BOF at 0.56, risk 100); the panel then dives until its frost fills the frame.
- **Highlights:** (1) the AI check, green 20 against red 100, with the real SHAP drivers and the real plain-words explanation; (2) signed twice, then one digit changed and every check fails; (3) the second claim on the same 5,000 t reverts.
- **Punchline:** "The same tonne cannot be sold twice." (the app's own sentence).
- **End card:** "EmbedProof", "A CO₂ claim you can check. A tonne it can't sell twice." (from the app's home headline), one proof chip ("178 contract tests · 100% coverage"), and the honest footer "Hackathon prototype · fictional companies, demo data".

## Visual identity

The web app already ships the Glass Layers look, so the video reuses its real tokens: base `#0B1020`; colour fields indigo `#3B2BFF`, teal `#00C2A8`, rose `#FF5C8A`; amber `#FFB547` only for the CBAM-default marker; text-safe tints `#35E8CC` / `#FF8FAE` / `#FFC66D`; glass is white at 12% with a dark scrim, 1 px white 30% edge, brighter top edge, `blur(24px) saturate(1.3)`. Type: Inter 700 and 400, Space Grotesk 700 for numbers, JetBrains Mono for the signed-document tile (all bundled from motion-kit `fonts/`, nothing from the network). At most four frosted panels on screen at once (peak: ledger, form, wipe, end card for a few frames). Captions live in a left column over a dark gradient scrim; every line is at least 28 px. No em dashes anywhere on screen.

## Real material

Own stack, so nothing else was disturbed: Anvil `:48545`, attestor `:48000`, web `:3300`. The web app runs from a copy of `web/` under `work/stack/web` with its own `node_modules` (installed offline from the pnpm store), because `web/.next` belongs to the other dev server. Contracts were deployed from a copy of `contracts/` into `work/stack/`, so the repo's `deployments/31337.json`, `contracts/broadcast` and `attestor/var` were not written. Seeded with `python -m attestor.demo` (5 of 5 scenarios passed), then the real UI was driven with Playwright to capture 2x PNGs of the real components with transparent backgrounds (`work/assets/`): the "Check plausibility" button, SHAP and plain-words tiles for both reports, the valid and FAILED signature badges, the "Change one digit" button, the certificate headline (all checks passed, then verification failed), the allocation ledger, the allocate form (Importer B, 5,000 t), and the real "Double claim blocked" panel with `DoubleClaimBlocked(1, 5000, 0)`. The gauge is the app's own SVG geometry with the needle and count driven by the video clock. Scores, SHAP values, ranges and the revert arguments come from the attestor's `/score` and the contract, not typed by hand.

Rebuilt rather than reused, and why: the declared-vs-expected bar (the app's text is about 11 px, unreadable at 1080p, so it is redrawn large from the same numbers and geometry) and the signed-document tile (a large replica of the app's editable document and its "What changed" diff: `seeTco2PerT 0.535` becomes `0.534`, which is exactly what the app's "Change one digit" does).

## Storyboard (120 BPM: beat 0.5 s, bar 2 s)

| # | Time | Scene | Performer | What happens | Sound |
|---|---|---|---|---|---|
| 1 | 0.0 to 3.0 | Hook | slow push, claim chip | Panel sharpens in, two lines of type; chip pops at 1.5 s; at 2.0 s the panel grows until its frost fills the frame | pop, whoosh |
| 2 | 3.0 to 8.0 | AI checks the claim | cursor, rack focus | Frost clears onto two result panels. Cursor clicks "Check plausibility" on the honest EAF mill: needle swings, counts to 20, green, tiles pop in. Rack focus: the BF-BOF panel comes forward; second click; needle sweeps teal, amber, rose to 100; the SHAP and plain-words tiles land. Captions: "An AI checks the claim." then "Honest EAF mill: risk 20" and "BF-BOF at 0.56: risk 100" | click, count, chime, switch, click, count, alert |
| 3 | 8.0 to 11.9 | Signed twice, one digit breaks it | cursor, shake | Frost clears; the real ECDSA and ML-DSA-44 badges tick valid; cursor clicks "Change one digit"; three FAILED badges slam in one by one with a shake, the document tile flips to the real diff, the headline chip turns to "verification failed". Captions: "Signed twice." then "One digit. Both fail." | tick, tick, click, glitch, thud x3 |
| 4 | 11.75 to 16.9 | Same tonne, second buyer | cursor, slam | The real ledger shows Importer A holding 5,000 t (row highlight); rack focus to the real allocate form; cursor clicks "Allocate"; the real "Double claim blocked" panel slams in with the rose halo and a shake. Captions: "Importer A holds 5,000 t.", "Importer B asks again.", "The same tonne cannot be sold twice." | whoosh, tick, switch, click, stamp, thud, alert |
| 5 | 16.9 to 20.5 | End card | frost clear, sheen | Frosted wipe, then the end panel sharpens: name, promise, proof chip. Holds about 3 s | whoosh, chime, final |

Pacing checks: `--audit` says lively (median 5.3% of pixels changing, 15% quiet), with no quiet stretch of 1.5 s or longer; the only stretch over 0.6 s is 18.0 to 19.0 s inside the held end card.

Constraint: readable captions (about 0.3 s per word) while something always moves. Failure modes: a stale or hung dev server (checked with real HTTP and Playwright captures, not log lines; the first compile of the copied app took 6.5 minutes); a frame that renders differently by time (the page is a pure function of t, fonts and every capture are awaited in `window.ready`); a quiet stretch (fixed from `--audit`). Ledger: no delegation, one agent, all built directly.

## Not done or worth knowing

- The bundled Inter and Space Grotesk subsets have no U+2082 (subscript 2), so "CO₂" in the video is a scaled digit; inside the real UI captures the subscript falls back to a system font (tiny text). Downloading a full font was not done.
- The Google Fonts download that `next/font` attempts failed in my dev copy (fallback used); fonts were injected from the local files for the captures.
- Gas figures are not shown (the demo measured 228,007 for `attest()` against the README's 228,019 final number); only the README and DEVPOST test and coverage numbers are on screen.
- Not on Base Sepolia: everything is the local Anvil chain, as the README says.
