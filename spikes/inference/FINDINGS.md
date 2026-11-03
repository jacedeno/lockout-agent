# Serverless Inference model eval (kickoff, 2026-11-03)

Subscription `agent-rush` (pay per token, no monthly allotment), key in
`$ARZ_SECRETS_DIR/vultr.env`. Script: `model_eval.py`. One sample per model, so treat
the latencies as rough.

Two tests per model:

1. **Code in a JSON-schema reply.** The model writes `velocity_rms_mm_s(accel_g, fs)` (10–1000 Hz
   band, frequency-domain integration). The code runs in the locked `arz-runner` container
   against 1 g at 100 Hz plus 0.5 g at 5 Hz (out of band). Expected: **11.04 mm/s**.
2. **Tool call.** `run_python(code, timeout_s)` against a CSV analysis request; pass = a valid
   call with parseable arguments.

## Results with `reasoning_effort: "low"`, `max_tokens: 8000`

| Model | $/M in / out | Code (s) | Tokens | JSON | Correct | Tool (s) |
|---|---|---|---|---|---|---|
| **qwen3.8-flash-next** | 0.10 / 0.20 | 5.3 | 2221 | yes | yes | 1.0 |
| qwen3.8-27b | 0.15 / 1.00 | 5.8 | 2750 | yes | yes | 1.2 |
| **glm-5.3** | 0.75 / 3.00 | 5.8 | 795 | yes | yes | 1.4 |
| glm-5.3-flash | 0.10 / 0.35 | 2.7 | 703 | yes | **no (NaN)** | 0.9 |
| deepseek-v4.1-flash-1017 | 0.15 / 0.60 | 21.0 | 2758 | yes | yes | 0.7 |
| mimo-v2.6-pro | 0.40 / 0.80 | 39.1 | 6620 | yes | yes | 1.5 |
| mimo-v2.6-flash | 0.10 / 0.25 | 9.2 | 2101 | yes | yes | 0.9 |
| minimax-m3.1-flash | 0.20 / 0.90 | 18.1 | 7113 | yes | yes | 0.9 |

All eight support `tools`, `response_format` (strict JSON schema honoured) and
`reasoning_effort`.

## Findings

- **Always set `reasoning_effort` and a generous `max_tokens`.** With the default effort and
  `max_tokens: 4000`, five of eight models spent the whole budget reasoning and returned empty
  `content` (`model_eval-2026-11-03.jsonl`). Reasoning comes back in a separate `reasoning` field.
- Tool calls work on every model. The `tool_ok: false` in the first run was a bug in the check
  (it required "fft" in the first call; models sensibly inspect the CSV first, some with two
  parallel calls).
- `glm-5.3` was the slowest to wake again: **63.6 s** on its first tool call (45 s on 2026-10-23).
  Warm it up before a demo.
- The model list changed since 2026-10-23: `deepseek-v4.1-flash` is now
  `deepseek-v4.1-flash-1017`. Pin model IDs in config, not in code, and re-check before Sunday.

## Choice

- **Default: `qwen3.8-flash-next`** — cheapest, passes both tests, ~1 s tool turns.
- **Escalation on a failed retry: `glm-5.3`** — fewest tokens to a correct answer, but 7.5× the
  price and a long cold start.
