# AI evaluation

There are two different kinds of evidence in this repository:

- `backend/tests/` checks calculations, scope, validation and workflow controls with simulated model responses.
- `scripts/evaluate_ai_workflows.py --live` makes real provider calls on synthetic examples and scores the results. These live scores have **not** been collected in the uploaded snapshot.

Test counts are not extraction accuracy or RAG quality. LangSmith traces show what ran; evaluation feedback scores whether the checked result matched its reference.

## Run all six component evaluations

From the project root, with the normal backend environment activated:

```bash
python scripts/evaluate_ai_workflows.py
```

Without `--live`, this only lists available cases and missing prerequisites. It makes no provider calls. Receipt images are generated in memory using the existing `check_image_extraction.py` fixture; no additional image asset folder is needed.

For voice tests, create `private_audio/evaluation/voice_en.wav` and `voice_de.wav`, each under 2 MB. Read these synthetic sentences:

- English: “On November tenth 2026 I bought two packs of milk at one euro nineteen each and one bread at one euro eighty-nine at Demo Markt. The total was four euros twenty-seven.”
- German: “Am zehnten November 2026 habe ich im Demo Markt zwei Packungen Milch zu je einem Euro neunzehn und ein Brot für einen Euro neunundachtzig gekauft. Insgesamt waren es vier Euro siebenundzwanzig.”

These recordings are local inputs and are excluded from Git. They are sent to the configured transcription provider when you explicitly run the voice evaluation.

Then run a small, charged evaluation:

```bash
python scripts/evaluate_ai_workflows.py --live --limit 2 --household-id YOUR_EVALUATION_HOUSEHOLD_UUID --user-id ITS_AUTHORIZED_MEMBER_UUID
```

The runner uses `backend/.env`. RAG needs an existing catalogue/index and an authorized evaluation household. It reads household data but does not seed purchases, save drafts, approve plans or reserve money. Missing voice files or RAG IDs are reported as blocked, not passed. Provider and parsing errors stay in the executed-case denominator.

| Component | Scored checks | Limits of this check |
| --- | --- | --- |
| Receipt extraction | DE/EN synthetic receipts; merchant, date, currency, total and order-independent numeric item lines | Not natural-receipt accuracy across layouts; wording exact match is a separate strict metric |
| Voice capture | Transcript word-error rate and subsequent purchase-field accuracy | Two clean recordings do not cover noisy speech, accents or the complete capture UI |
| Product matching/RAG | Existing paired baseline/RAG labels: hit@1, hit@3, abstention, suggestion precision and actual semantic evidence | Exact-memory matches do not prove semantic retrieval; zero retrieved correction evidence means no measured correction-RAG contribution |
| Ask Bonwise | Real structured routing, resolved dates, code-calculated facts and reference validity on fixed records | Not the complete authenticated API/tool chain or a multi-turn browser conversation |
| Purchase insights | Allowed evidence-backed selections and inclusion of an eligible budget constraint | Human usefulness is not scored automatically; fallback/freshness are regression tests |
| Planner | Edit parsing and candidate selection within known cost bounds | Not a live approval, reservation or durable-checkpoint test |

Use `--limit 5` after inspecting smoke results and cost. This includes related/unsupported chat cases. Dedicated datasets contain 16 RAG cases and 25 planner edits; the selected sample counts are recorded. Increasing the limit means more charged calls.

## Results and LangSmith

The runner saves one sanitized report: `evaluation-results/live-ai-components.json`. It contains case IDs, scores, errors, latency, model/prompt versions, source/dataset hashes and coverage. No raw receipts, audio, model answers, tokens, household IDs or transcripts are stored in it.

When `LANGSMITH_TRACING=true` and credentials are configured, it sends a content-free `bonwise_evaluation` run and attaches numeric feedback. Open the configured LangSmith project and find that run. `langsmith.trace_sent` and `feedback_sent` in the JSON identify whether delivery succeeded. Local results remain available if feedback delivery fails.

This is **component evaluation feedback attached to a trace**, not a hosted end-to-end LangSmith experiment. The RAG wrapper's completion flag is not matching accuracy: inspect its paired metrics and semantic evidence count. Automatic usefulness, measured cost and production security certification are not claimed.

For a focused run, the existing tools remain available:

```bash
PYTHONPATH=backend python scripts/evaluate_correction_rag.py --live --limit 16 --household-id YOUR_HOUSEHOLD_UUID --user-id ITS_MEMBER_UUID
PYTHONPATH=backend python scripts/evaluate_planner.py --live --limit 25 --output evaluation-results/planner-live.json
```

Review real results before adding scores to README or a résumé. Report sample size, fixture scope, model, failures and limitations alongside the number. Keep the current project architecture and model-interpretation claims separate from unmeasured accuracy claims.

Transcript WER uses fixed lowercasing and punctuation removal. Number-word/digit differences can count as word errors even when extracted monetary fields are correct; purchase-field scores are reported separately.
