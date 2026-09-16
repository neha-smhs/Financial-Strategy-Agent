# Final run token usage and cost

- Generated at: 2026-09-16T09:31:01.760884+00:00
- Output SHA-256: cc134e189324cbc3e87e6c0c56c9f31ea07aebcdc05c6be5d7967f9e10735b33
- Requests: 250
- Runtime: 24.17 seconds
- Backend: local

| Provider / model | Calls | Input tokens | Output tokens | Estimated API cost |
|---|---:|---:|---:|---:|
| Local / tesseract 5.3.4, LSTM OEM 1 | 22 OCR passes | N/A | N/A | $0 |
| Local bilingual financial-fact rules | No generative calls | 0 | 0 | $0 |

- Total LLM calls: 0
- Total input / output tokens: 0 / 0
- Total tokens: 0
- Average tokens per request: 0.00
- Total estimated API cost: $0
- Average estimated API cost per request: $0
- Images referenced: 11; image cache hits: 0

Tesseract is a local neural OCR model and does not report LLM tokens. API cost excludes local compute and development-assistant usage. Development chat tokens are not exposed to this application and are not represented as measured runtime usage. A cold-cache run is recommended for the final report. Cached extraction creation costs belong to the earlier run and are not silently included as new calls.
