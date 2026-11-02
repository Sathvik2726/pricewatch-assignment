---
name: "Sathvik Naidu"
median_basis: "observations"
levels_attempted: [1, 2, 3, 4, 5]
llm_provider: "anthropic"
llm_model: "PRICEWATCH_MODEL (runtime configurable)"
ai_tools_used: ["ChatGPT"]
hours_spent: 1
---

# Decision log

## Stage 1 — the false alerts

The Maple adapter selected the first price node, which can be the struck-through reference price, so unchanged current prices could appear to jump. The drop formula also divided by the new price; it reported 25% for a change from 100 to 80 instead of the conventional 20%. I now prioritize sale/current-price selectors, exclude comparison prices, and divide by the previous amount. Drop comparisons also require the same currency. The regression assertion was corrected to the actual percentage definition.

## Stage 2 — the median

I use every valid prior observation in the trailing time window, ending immediately before the current observation, as ISSUE-2 specifies. A daily-close median would give days equal weight, but that conflicts with the issue's explicit “all observations” acceptance contract. Fewer than three valid observations, no prices, malformed timestamps, or mixed currencies produce no median alert. The median is rounded to an integer minor unit. When both rules match, `below_median` takes precedence.

## Levels 3–5 — how you got in

Zon's prior adapter depended on deployment-specific class hashes. It now reads the main offer area before the separately labeled sellers, ignores struck-through and per-unit values when a pack total is available, and follows the first advertised variant when selection is required. Shield's HTTP 503 contains a JavaScript proof challenge; the shared client answers the proof using the page's current challenge values, waits the served delay, preserves the resulting cookie, and reads the product's inert JSON state. Flux's delivered script declares its API route, query, build id, signature inputs, and whether amounts are major or minor units; the adapter reads that contract and normalizes the offer. A fresh-seed scan revealed two more contract variations: Flux used SHA-1 with a bundle-declared signature length, and Shield changed its proof input transform and digest slice. Both paths now read those details from the served scripts. With those changes, a fresh-seed local scan priced all 56 products. These paths use only content served by the mock stores and obey each robots policy and crawl delay.

The full hosted scan found 56 products: Corner 10/10 priced, Maple 12/12, Shield 10/10, Flux 12/12, and Zon 10/12. Zon returned two catalog cards with an em dash and no detail-page offer; for nine others, its detail page withheld the offer but its product card showed exactly one price, which the scout retained as a fallback. All requests obeyed the hosted robots crawl delay.

## Stage 3 — what the model got wrong

The Anthropic path uses `PRICEWATCH_MODEL` with a default and preserves `metadata.source_url`. The extractor strips noisy markup while retaining JSON-LD, validates output, tolerates fenced or malformed JSON, and can cache raw responses by provider, URL, and page content. The eval now counts provider failures as incorrect and reports accuracy by store and field, errors, latency, and baseline. The provider protocol exposes no token usage or pricing, so the harness cannot report actual token counts or a defensible cost estimate. I could not run a real-key eval in this environment; no `eval/out` model report is claimed.

I reviewed the snapshots and added eight likely label issues to `eval/label_issues.json`, with one-line evidence for each. The coupon-dependent `bz-066` and the tax-inclusive `nk-008` cases are especially arguable. The real Anthropic eval is still unrun because no API key was available; therefore `eval/out/report.json` is not claimed as a model result. The CLI can infer a fallback store label from an unfamiliar URL, and the parser accepts `currency: null` when no offer is present, as its output schema allows. The full test suite passes: 29 tests.

## Where AI helped and where it didn't

ChatGPT helped map the existing modules and draft the first implementation pass. The important corrections came from checking the issue contract against the starter tests and inspecting the provided HTML: the original percentage assertion encoded the buggy denominator, and `kr` needed locale-aware parsing. I used the explicit ISSUE-2 wording for the median basis where the broader README describes daily closes.

## If I had another day

Run the grader's fault-injected eval and, when an Anthropic key is available, a real model eval; validate the eight candidate label issues against the answer key. Then test a fresh local container seed, especially Zon’s two no-offer cases.
