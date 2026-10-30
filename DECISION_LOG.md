---
name: "Sathvik Naidu"
median_basis: "observations"
levels_attempted: [1, 2, 3, 4, 5]
llm_provider: "anthropic"
llm_model: "PRICEWATCH_MODEL (runtime configurable)"
ai_tools_used: ["ChatGPT"]
hours_spent: 2
---

# Decision log

## Stage 1 — the false alerts

The Maple adapter selected the first price node, which can be the struck-through reference price, so unchanged current prices could appear to jump. The drop formula also divided by the new price; it reported 25% for a change from 100 to 80 instead of the conventional 20%. I now prioritize sale/current-price selectors, exclude comparison prices, and divide by the previous amount. Drop comparisons also require the same currency. The regression assertion was corrected to the actual percentage definition.

## Stage 2 — the median

I use every valid prior observation in the trailing time window, ending immediately before the current observation, as ISSUE-2 specifies. A daily-close median would give days equal weight, but that conflicts with the issue's explicit “all observations” acceptance contract. Fewer than three valid observations, no prices, malformed timestamps, or mixed currencies produce no median alert. The median is rounded to an integer minor unit. When both rules match, `below_median` takes precedence.

## Levels 3–5 — how you got in

Zon's previous adapter depended on deployment-specific class hashes. It now looks for semantic price attributes and standalone currency amounts, excluding struck-through and per-unit values; requests still pass through the shared robots and retry-aware client. This is a heuristic and may not distinguish every marketplace offer from shipping or alternate sellers. Shield retries a soft browser-check page once with the same cookie session and extracts semantic HTML prices. Flux checks semantic HTML and same-origin API references in inline application scripts; it does not execute JavaScript or reverse engineer undocumented endpoints, so the SPA level remains incomplete if its API is only embedded in external bundles or requires app-specific request signing.

The provided local test suite and focused edge tests pass. I did not have the test-store service running, so I have not confirmed full catalogue scans or the level 3–5 behavior against a live seed.

## Stage 3 — what the model got wrong

The Anthropic path uses `PRICEWATCH_MODEL` with a default and preserves `metadata.source_url`. The extractor strips noisy markup while retaining JSON-LD, validates output, tolerates fenced or malformed JSON, and can cache raw responses by provider, URL, and page content. The eval now counts provider failures as incorrect and reports accuracy by store and field, errors, latency, and baseline. The provider protocol exposes no token usage or pricing, so the harness cannot report actual token counts or a defensible cost estimate. I could not run a real-key eval in this environment; no `eval/out` model report is claimed.

While manually comparing sample snapshots with their visible text, I found several likely label errors: `nk-003` labels the crossed-out old price instead of the shown current price; `bz-047` labels M.R.P. instead of Deal Price; `bz-052` labels EMI/month instead of the full offer; `bz-058` labels the per-kilogram amount instead of the pack price; and `bz-073` labels a per-unit amount instead of the three-unit pack total. These are candidates pending evaluation against the hidden answer key; I have not written an exhaustive label issue file.

## Where AI helped and where it didn't

ChatGPT helped map the existing modules and draft the first implementation pass. The important corrections came from checking the issue contract against the starter tests and inspecting the provided HTML: the original percentage assertion encoded the buggy denominator, and `kr` needed locale-aware parsing. I used the explicit ISSUE-2 wording for the median basis where the broader README describes daily closes.

## If I had another day

Run the five-store container with a fresh seed, inspect browser network behavior for Shield and Flux, add protocol-level tests for those interactions, run the fault-injected eval and a real Anthropic eval, and verify candidate label issues against the grader's expected behavior.
