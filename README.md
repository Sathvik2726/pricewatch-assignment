# PriceWatch — i95Dev AI Engineering take-home

Welcome. This is the take-home assignment for the AI Engineer (intern / entry-level) role at
i95Dev. You will inherit a small, working, imperfect system, improve it, and show that your
changes work. Everything you need is in this repository plus a running instance of the
PriceWatch test stores, described in `STORES.md`.

**Please read this page in full before you start. It is the single source of truth for how
the assignment works and how it is graded.**

---

## 1. Using AI tools

We build software with AI tools every day and we expect you to use them here: Claude, Cursor,
Copilot, ChatGPT, Gemini, an agent — whatever you normally use, for code, tests, debugging,
reading the codebase, or writing your notes. There is no penalty for using AI and no "AI
detection". We ask two things:

1. **Understand what you submit.** After you submit, there is a 30-minute call in which we
   change a requirement and watch you modify your own code. Code you can't explain or extend
   won't help you there.
2. **Tell us how you used it.** `DECISION_LOG.md` has a section for this. Honest, specific
   notes ("the model kept inventing a CSS selector that doesn't exist") are far more useful to
   us than a polished summary.

The tasks reward reading the code, running it, and checking the output against what the
stores actually serve. Pasting a task into a chat window and shipping the answer tends to
score poorly.

## 2. What you're building

PriceWatch is a set of small agents that track prices across online stores:

```
scout ──► fetch ──► extractor ──► normalizer ──► history ──► watcher ──► alerts
   (find product URLs)   (HTML → data)  (money → cents)  (SQLite/JSONL)  (rules)
```

It watches five test storefronts served by the PriceWatch test stores. They range from
straightforward to difficult and reproduce the kinds of friction real sites put in front of
automated clients. You get a running instance and a description of *symptoms*; working out
the mechanisms is part of the assignment.

| Level | Store | What you'll notice |
|---|---|---|
| 1 | Corner Store | nothing — clean, well-structured HTML |
| 2 | Maple & Co | Shopify-like; European price formatting; sale items show two prices, but not always; variants, and the default isn't always first |
| 3 | Zon | marketplace; prices in pieces; nothing stable in the markup; several prices per page, one of them real; picks a variant first sometimes; suspicious of clients that don't *behave* like browsers, and silent about it |
| 4 | Shield Outfitters | won't let you in at first; a browser gets in after a moment; access is neither permanent nor portable; dislikes speed; price isn't findable by text search |
| 5 | Flux | single-page app; no prices in HTML; the API only talks to the app's own JavaScript; "amount" doesn't mean the same thing for every product; flaky; layout varies; prices really change |

`STORES.md` has a slightly longer version of this table and nothing more. Nothing is hidden
from you that a browser doesn't also have to deal with — open DevTools and watch.

**Do not scrape any real website for this assignment.** Everything runs against the test stores.

## 3. Setup (10 minutes)

You need Python 3.10+ and Docker (or use the hosted stores instance for light development).

```bash
# 1. The test stores (Docker; or use the hosted instance at https://pricewatch-stores.vercel.app for light development)
docker run --rm -p 4000:4000 -e STORE_SEED=public ghcr.io/i95dev/pricewatch-stores:latest
#    ^ leave this running. Change STORE_SEED to test against a catalogue you haven't seen.

# 2. This repository, from its root directory
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
pytest                                          # one test fails; see tasks/ISSUE-1.md
pricewatch scan --stores-url http://localhost:4000 --store corner
```

If the last command prints ten JSON lines with prices, you're set. Open
http://localhost:4000 in a browser and look through the five stores before writing any code.

Anthropic is included in the base install because the grader runs the Anthropic extractor with
its own `ANTHROPIC_API_KEY`. For local Stage 3 work, export your own `ANTHROPIC_API_KEY` (or
install `.[llm]` and use `OPENAI_API_KEY` if you prefer OpenAI). You pay for your own development
usage; the whole assignment should cost well under $2. Grading uses **our** Anthropic key against
your `--provider anthropic` path, so make sure that path works even if you developed with another provider.

## 4. The work

Everything is described in `tasks/`. Do the stages in order; each builds on the last.

| Stage | File | What | Time (guide) |
|---|---|---|---|
| 1 | `tasks/ISSUE-1.md` | A bug report about false price-drop alerts. Fix it. | ~1.5 h |
| 1b | — | Climb the levels: make `pricewatch scan` return correct prices for as many of the five stores as you can. Each level is graded separately; partial credit is real. Levels 3–5 are where most of the time goes. | ~3 h |
| 2 | `tasks/ISSUE-2.md` | Add a `below_median` alert rule. | ~1.5 h |
| 3 | `tasks/ISSUE-3.md` | LLM extraction for unknown stores **plus the eval that proves it works**. We grade the eval. | ~2 h |
| — | `DECISION_LOG.md` | One page: what you decided, what you found, where AI helped and where it didn't. Fill in the frontmatter — the grader reads it. | 20 min |

Guide total: 8–10 hours. You have **7 days** from receiving the link. We don't expect all five
levels plus a perfect eval; we do expect clear thinking about what you did and didn't do.

### Ground rules

- Keep the CLI contract in `pricewatch/cli.py` stable — the autograder drives it.
  Add flags if you want; don't rename or remove existing ones.
- Keep the `Observation` fields. Add fields if you want.
- Respect `robots.txt` and `Retry-After`. The grader checks both.
- The existing tests describe intended behaviour as someone understood it at the time. If you
  change or remove a test, explain why in `DECISION_LOG.md`.
- Don't hard-code product IDs, prices, markup details, or anything else you observed under one
  seed. The grader uses a different seed, and the seed changes more than the catalogue.
- Treat the stores as a black box: work only from what they serve over HTTP, as you would with
  a real website. Extracting, decompiling, or otherwise inspecting the container image is out
  of scope.
- Python is the starting point. If you'd rather work in TypeScript, you may port the whole
  project — the autograder only talks to the CLI — but the port time is yours.

### Alert rules (reference)

Rules live in `alerts.yaml` and are evaluated by `agents/watcher.py` after each scan.

- `drop_pct` — fires when the price falls at least `pct` percent versus the previous observation.
- `below_median` — (ISSUE-2) fires when the price is at least `pct` percent below the
  product's median over the trailing `window_days`. The median is taken over daily closing
  prices — the last observation on each day — so that a day with many scans doesn't
  outweigh a day with one.

## 5. Submitting and grading

Read `SUBMISSION.md`. In short: push to a **private** GitHub repository, install the
[PriceWatch Grader app](https://github.com/apps/pricewatch-grader/installations/new) on it,
submit the URL at **https://pricewatch-submit.vercel.app**, confirm the link we email you,
and receive a score card by email within about 15 minutes. You can submit **three times**; the
last submission counts. We recommend submitting once early to confirm the grader can install
and run your project.

Scoring (100):

| Points | What | How |
|---|---|---|
| 20 | Stage 1 | hidden tests: no false alerts; correct Maple prices |
| 20 | Levels 1–5 extraction | hidden product set; weighted by level (L1 = 1 … L5 = 6) |
| 15 | Stage 2 | hidden history fixtures |
| 20 | Stage 3 | 12: harness survives our fault-injecting provider, report schema, `label_issues.json` vs. answer key · 8: **your** LLM extractor run with **our Anthropic key** (`--provider anthropic`) on 40 never-seen pages from two unknown stores |
| 10 | Code & design | reviewed by an engineer: is this a codebase you'd want to inherit? |
| 5 | Decision log | reviewed by an engineer: clarity, honesty, specificity |
| 10 | Walkthrough | live, 30 minutes; we change a requirement and you make the change |

The first 75 points are scored automatically and determine who is invited to a walkthrough.
The remaining 25 are assessed by an engineer. The walkthrough is required: a strong automatic
score does not make up for being unable to explain or change your own code.

## 6. Questions

Reply to the email that sent you this assignment. We answer questions about setup and about
the grader within one working day. We don't answer questions about which interpretation of a
spec is "right" — deciding that, and writing down why, is part of the job.

Good luck.
