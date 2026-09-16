# Buy or Wait financial strategy agent

Post-submission revision of the September 2026 challenge solution. Reads financial CSVs, interprets evidence, forecasts all supported recurring commitments over 90 days, and compares full, partial, installment and deferred payments.

## What changed

- Removed file-ID amount lookups from the runtime. Local mode reads actual PNG pixels with Tesseract LSTM OCR. A synthetic unseen receipt is included in the tests.
- Added an optional language/vision-model evidence backend with a complete validated fact schema, source quotes, content-addressed caches, bounded API calls and output tokens, and refusal/error handling. No live model run has been measured for this revision: no API key was available.
- Uses the same baseline forecast for safe capacity, earliest full-payment date and plan selection. Safety continues through day 90, even if the purchase finishes sooner.
- Checks intermediate balances with debits before credits by default. Purchases happen after that day's posted flows. Opening balance is checked too.
- Missing future debit amounts block approval; missing FX quotes raise an error instead of becoming zero. Pending credits and unsettled speculative credits are excluded.
- Spending reductions apply only to selected recurring events. Payment schedules, totals, fees, user permissions, dates and protected categories are checked.
- Separate arithmetic replay validates serialized payment plans. It shares the reconstructed evidence and forecast inputs; it is not an independent source-of-truth forecast.

## Setup and local run

Python 3.10+ and the Tesseract command-line program with English traineddata are required. Runtime Python code uses the standard library. On Ubuntu, install OCR with `sudo apt-get install tesseract-ocr`; on macOS use `brew install tesseract`.

Run from the repository root:

```bash
python code/main.py
python code/validate_output.py
python code/evaluate.py
python code/compare_revision.py
```

The final file is `challenge/output.csv`. Run reports are in `challenge/evaluation/`. Detailed forecast traces and content-addressed evidence caches are generated locally and ignored by Git.

For all regression tests, install Pillow for the synthetic receipt fixture:

```bash
python -m pip install Pillow
python -m unittest discover -s code -p 'test_*.py'
```

## Optional model mode

Set environment variables outside source control:

- `OPENAI_API_KEY`: provider credential.
- `BUY_OR_WAIT_MODEL`: an available model supporting images and Chat Completions JSON output.
- `OPENAI_BASE_URL`: optional compatible HTTPS endpoint; defaults to `https://api.openai.com/v1`.
- `BUY_OR_WAIT_MAX_API_CALLS`: includes retries, default 600 per process/run.
- `BUY_OR_WAIT_MAX_OUTPUT_TOKENS`: default 800 per call.
- `BUY_OR_WAIT_INPUT_USD_PER_MILLION` and `BUY_OR_WAIT_OUTPUT_USD_PER_MILLION`: provider prices for measured cost reporting. These are reporting inputs, not a dollar spending cap.

```bash
python code/main.py --backend api --evaluation-dir evaluation/api --output output_api.csv
python code/validate_output.py --backend api --output output_api.csv --report evaluation/api_validation.json
python code/evaluate.py --backend api --report-dir evaluation/api_samples
```

The model extracts facts only; it cannot select or approve a payment. Message quotes must occur in the supplied source. Image quotes cannot be independently verified against pixels by the schema checker. Schema validation checks structure and types, not factual truth. Invalid model evidence fails the run without writing a partial output. Cache misses invoke the API; this is not a guaranteed offline mode. The program reads environment variables and does not automatically load `.env`.

## Measured revision results

Local OCR + rules; 25 public development examples; identical scoring for submitted baseline and revised code:

| Metric | Submitted baseline | Revised |
|---|---:|---:|
| Affordability status | 60% | 72% |
| Payment method | 68% | 84% |
| Payment plan, normalized amounts | 68% | 84% |
| Earliest full-payment date | 40% | 64% |
| Amount within 5% of requested amount | 36% | 72% |
| Amount within one cent | 12% | 8% |
| Spending changes | 88% | 72% |
| Mean amount error / requested amount | 20.21% | 8.91% |

All 250 requests produce output. 185 recommended plans pass independent arithmetic replay; 65 requests are not recommended, including 7 with unpriced essential obligations. These results do not establish hidden-test accuracy or real-world safety. Payment-plan baseline here is 17/25 after numeric normalization; the old README reported 13/25. Do not compare differently formatted plan strings as if they were different cash payments.

See `evaluation/comparison.md`, `evaluation/output_validation.json`, and `evaluation/test_results.txt` inside `challenge/` for measured evidence. `evaluation/baseline/submitted_engine.py` preserves the old engine solely for comparison; its file-specific amounts are never imported by the revised inference path.

## Architecture

`main.py` joins request data and calls `evidence.py` / `api_evidence.py` for extraction. `forecast.py` builds cash-flow events and a consistent 90-day forecast. `planner.py` enumerates and ranks eligible plans, then replays payments. `validate_output.py` parses the exported CSV and verifies schedules and balances again. `evaluate.py` and `compare_revision.py` load public labels only for scoring, never as inference features.

## Assumptions and remaining limitations

- Settled historical transactions are already included in opening balance.
- Recurrence is inferred, not guaranteed. Local salary reconstruction and narrow bilingual message rules still need broader testing for multiple income streams and unfamiliar wording.
- Variable spending uses a median estimate; unusual future bills may exceed it.
- OCR is general rather than file-specific, but can misread handwritten totals or decimal digits. A successful OCR call does not prove the amount is correct. Extracted text and candidate amounts are retained for review.
- Unresolved past settled images are disclosed and excluded from recurrence. Unknown future obligations prevent safety certification.
- The model schema checks and network path were tested using mocked responses. A live model evaluation remains outstanding.
- Use ordinary Python, not `python -O`: contract checks use assertions.
- No other project's implementation or model caches were copied. Architecture inspiration: separation of model-assisted evidence from deterministic financial decisions in https://github.com/trickymind1324/personal-finance-agent. This revision reuses and extends our own earlier modular implementation.

## Packaging

From `challenge/`:

```bash
python code/package_submission.py --destination ../revision-deliverables
```

The package includes source, dataset/media, predictions and evaluation evidence; excludes secrets, caches, generated traces and chat logs. Usage reporting distinguishes local neural OCR from LLM calls. API costs exclude local compute and development-assistant usage.

Generated ZIP archives are kept out of Git; recreate the current archive with the packaging command above. The original submitted archive remains available in Git history.

## Published branch artifact policy

This branch publishes source and aggregate reports only. The committed `output.csv` remains the original submission; run the generator to create revised predictions. Row-level sample predictions, OCR evidence, and conversation logs are not published with this revision. Evaluation scripts regenerate detailed reports locally.
