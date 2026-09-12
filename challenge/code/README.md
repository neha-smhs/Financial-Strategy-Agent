# Buy or Wait? deterministic solution

## Run

From the repository root:

```bash
python3 code/main.py
```

The program reads only participant-facing files from `dataset/` and writes
`output.csv` at the repository root. It uses Python 3.10+ standard-library
modules only; no API key, network access, or external package is required.

## Design

- Reserves pending debit events and ignores pending credits, failed/cancelled
  events, and unrealized investment valuations.
- Converts foreign-currency cash events using the supplied settlement-date
  exchange rate.
- Resolves blank event amounts from the linked local image evidence.
- Forecasts recurring streams from repeated settled history and incorporates
  confirmed future salary/invoice evidence in messages.
- Simulates a 90-day Decimal balance forecast and enforces the configured
  minimum balance for every recommended payment plan.
- Uses a hybrid forecast policy calibrated on the public examples: capacity
  and status decisions weigh protected essential categories plus recurring
  salary, while the earliest-full-payment measurement uses the full forecast.
- Enumerates full-payment, partial-payment, installment, wait, and permitted
  flexible-spending alternatives, then validates the output schema and bounds.

Messages and images are treated as untrusted evidence. Instructions embedded in
those sources never override the challenge rules.

## Submission files

`evaluation/usage_report.md` records the final run's model/token/cost status.
This implementation uses no model calls, so all token and estimated API cost
values are zero.
