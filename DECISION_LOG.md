---
name: "Sathvik Naidu"
median_basis: "observations"
levels_attempted: [1, 2, 3, 4, 5]
llm_provider: "openai-compatible (Ollama local smoke test)"
llm_model: "qwen3:0.6b; no full real-key eval completed"
ai_tools_used: ["ChatGPT"]
hours_spent: 1
---

# Decision log

## Stage 1 — false alerts

The Maple selector previously used a combined CSS selector, which returns the first matching
element in document order. A comparison price or head metadata could therefore beat a sale or
current price. The extractor now checks selectors in priority order and skips struck-through,
comparison, and per-unit amounts. The drop percentage is divided by the previous price; the
reported 100-to-80 change is 20%, not 25%. Zon can withhold detail offers. I kept its
single-price catalog-card fallback behind an explicit store setting, and it now follows a
variant only when the page marks that variant selected. I have not supplied a real-browser
Network capture, so the remaining Zon request-behavior difference is not established.

## Stage 2 — median

The default basis is all valid observations, matching the ISSUE-2 acceptance tests and the
starter's allowed "observations" token. A rule may opt into "daily_close", using the last
observation in each UTC day. Timestamps are normalized to UTC. The trailing window excludes
its exact start and the current observation. I compare against the exact median with decimal
arithmetic and round only the alert's displayed median half up. Fewer than three prices or
mixed currencies produce no alert. "below_median" has priority over "drop_pct".

## Levels 3–5 — store behavior

The shared HTTP client now applies longest-match robots rules with wildcard and end-anchor
support, observes Crawl-delay, and does not retry before a long Retry-After. It also retries
transient 5xx responses. Shield's challenge is solved by the shared client; its extractor
reads only the resulting inert offer state and converts major/minor amounts. Flux reads a
same-origin application contract, caches the bundle for one scan, retries stale offers, and
does not report a price that remains marked stale. On the latest public-seed scan, 56 products
were found: Corner 10/10 priced, Maple 12/12, Shield 10/10, Zon 10/12, and Flux 10/12. Two
Zon pages had no selected primary offer or unambiguous card price; two Flux offers stayed
stale after retries. These are known limits, not claims of full extraction coverage.

The scan has a 200-second global deadline, stops scheduling new products when it expires, and
flushes each completed observation to JSONL. Catalog hints are passed explicitly instead of
being attached to the shared client, avoiding a cross-store race.

## Stage 3 — unknown-store extraction

The Anthropic SDK path no longer sends "temperature", limits retry time, records usage, and
wraps unexpected SDK failures. The CLI returns a JSON Observation for LLM-path failures.
The extractor asks for printed price text, normalizes ISO currency and minor units (including
zero- and three-decimal currencies), preserves metadata and struck-price cues, checks that
the quoted digits occur in the page, uses JSON-LD as a fallback, and retries malformed JSON
once. The eval runs six pages concurrently in label order, classifies timeouts by exception
type, marks malformed records wrong, reports estimated token cost, and locates label issues
from common grader paths.

I added ten suspected label issues; nk-014 (six-pack) and nk-022 ("Utsolgt") are directly
visible in their saved pages. The other entries remain candidates and should be compared with
the grader's answer key. The full Anthropic 90-page run and hidden-store Part C have not been
verified with a real Anthropic key. My local qwen3:0.6b smoke test returned incorrect currency
and pack size on one product, so it is not evidence of model accuracy. The grader attempt 2
score was 66.2/75; its report said all 40 hidden extractor invocations exited nonzero or
printed no JSON. The Anthropic compatibility fix addresses the likely SDK crash, but only a
new grader attempt can establish the score.

## Where AI helped and where it didn't

ChatGPT helped inspect the code and identify the unsupported Anthropic "temperature" argument.
Its first-pass recommendations about Maple variants and Zon browser behavior need real page
and DevTools evidence; I did not infer those request details. I tested local behavior with
saved HTML, unit tests, the echo eval, and the running public Docker store.

## If I had another day

Capture a Maple product page and Zon index/product request sequence in browser DevTools, then
verify those adapters against the grader truth on a fresh seed. Run the saved-page eval with
an available real provider, inspect the report, and use the final submission attempt only
after confirming the Anthropic CLI emits usable JSON with the grader's runtime settings.
