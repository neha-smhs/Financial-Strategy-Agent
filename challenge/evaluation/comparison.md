# Revision evaluation

25 public development examples, not an untouched holdout or hidden test. API backend not run.

Both versions rerun and scored with identical numeric payment-plan normalization. Baseline source is the submitted GitHub engine at e075481. Revised default is local OCR plus rules, all recurring expenses, 90-day horizon, debits before credits. No other repository code or cached model results were copied.

| Metric | Submitted | Revised |
|---|---:|---:|
| status | 15/25 (60%) | 18/25 (72%) |
| method | 17/25 (68%) | 21/25 (84%) |
| plan | 17/25 (68%) | 21/25 (84%) |
| earliest | 10/25 (40%) | 16/25 (64%) |
| changes | 22/25 (88%) | 18/25 (72%) |
| amount_within_cent | 3/25 (12%) | 2/25 (8%) |
| amount_within_5pct_requested | 9/25 (36%) | 18/25 (72%) |

Mean absolute safe-amount error / requested amount: 20.21% -> 8.91%.

Safety validation independently replays the reconstructed cash-flow events, but shares the evidence and recurrence assumptions. It cannot establish real-world safety or correctness of all extracted facts. Unknown historical image amounts are disclosed and excluded from recurrence; unknown future obligations block approval.

Limitations: no live API credential was available. Model transport/schema tests use mocks; no claim of measured LLM accuracy or cost is made. OCR totals need review on ambiguous layouts. Local messages use bounded patterns, not general language understanding. No hidden-test labels were accessed. Changes were not tuned to another project’s reported scores.
