English · [简体中文](./README.zh-CN.md)

[![CI](https://github.com/1438388098-glitch/statute-rag/actions/workflows/ci.yml/badge.svg)](https://github.com/1438388098-glitch/statute-rag/actions/workflows/ci.yml)

# statute-rag · Article-level hybrid statute retrieval

A statute-retrieval pipeline that treats the **article** as the retrieval unit and **verifiable citations** as a hard constraint, with a **fully reproducible offline evaluation**. Structured article-level chunking → character-bigram BM25 (original + synonym-expanded query) / LIKE multi-channel RRF fusion → forced article-level citations. On top of that frozen lexical baseline sit three **optional, off-by-default** layers: the **semantic reranking layer (v7)** and the **LLM / Jev rerank layers**.

**Version: v0.1.1 tag. `main` adds, on top of it: a measurement foundation (depth/k decoupling, Recall@k curves), pip-installable packaging, the `Reranker` interface with the v7 semantic layer plugged into it, and the optional LLM/Jev rerank layers (all unreleased — see [CHANGELOG.md](CHANGELOG.md)). The core stays zero-dependency; the semantic layer needs `numpy + transformers + torch` (CPU is enough) and the LLM/Jev layers need an API key. Every layer is off by default, and a plain `HybridRetriever` reproduces the published lexical numbers bit for bit.**

## Quick start (no corpus required, ~30 seconds)

Core: pure Python standard library, zero third-party runtime dependencies. Python ≥ 3.8 (CI runs 3.8, 3.9 and 3.13).

```bash
# 1) Generate the synthetic demo corpus (~100 programmatically generated
#    fake articles + a synthetic gold set, fixed seed, reproducible)
python scripts/make_demo_corpus.py

# 2) Evaluate the retrieval trio on the demo corpus
python scripts/run_eval.py --corpus demo_corpus/corpus.jsonl --gold demo_corpus/gold.jsonl --out-dir demo_corpus

# 3) Search demo
python scripts/search_cli.py --corpus demo_corpus/corpus.jsonl "台账公示" --k 2
```

> Demo scores **only verify that the pipeline runs end to end** (template-generated fake articles are trivially separable); they say nothing about real-world retrieval quality. For the real numbers see "Evaluation".

Optionally install as a package: `pip install .` (the synonym dictionary ships as package data; `import statute_rag; statute_rag.__version__`).

## Web app (an app-style UI, zero frontend dependencies)

```bash
python scripts/app.py                                      # reads data/corpus_v7.jsonl (the current corpus)
python scripts/app.py --corpus demo_corpus/corpus.jsonl    # demo corpus if you have no real one
```

Starts a local server (default `http://127.0.0.1:8787/`) and opens a browser. The UI is a search box plus result cards (law name, article number, full text, matched fragments underlined in blue), plus **browse by law** (a per-law index with article counts, click through to full texts) and a **local search history** (re-run / delete one / clear all). Every result shows **which channels hit it** (literal / expanded / exact-substring), offers one-click copy of the article with its citation, and when a colloquial query was rewritten by the dictionary the actual search string is displayed — so "why did this article come back" is visible rather than a black box. With the optional Jev rerank layer enabled, the depth-reranked view also shows each result's Jev judgement score.

Three rules hold this together:

- **The UI and the evaluation share one `HybridRetriever` instance and one fused ranking.** The hit-source trace comes from `HybridRetriever.recall_with_trace()` — read-only, never scored; `recall` is literally implemented on top of it (bit-for-bit identical). So "the ranking the UI shows is the ranking we evaluate" is a structural fact, not a promise, and there is no separate demo logic to drift.
- The frontend is a single file, [app/index.html](app/index.html): **no build step, no npm, no external scripts or webfonts**, works offline.
- The server binds `127.0.0.1` only and maps no filesystem paths (just the embedded page plus two JSON endpoints, `/api/meta` and `/api/search`), so there is no directory-traversal surface. Exposing it to a LAN or the internet requires an explicit `--host` plus your own reverse proxy — not something this project does by default.

> The corpus is not distributed with the repo, so run the app **where the corpus lives**. Every number the UI shows (article/law counts, index build time, dictionary size) is read from the corpus and code, never hardcoded.

## The numbers (current scope: v7 corpus = v6 + a pure 361-article append, 25,987 articles / 444 laws; gold sets: synthetic v6 (177 questions), real-question v6 (38 questions), blind bank round 1 (100 questions, the v7 tuning set) and round 2 / v8 (100 questions, holdout))

> **Scope-change notice**: since 2026-10 all 14,212 v1 “gazette page-block” rows have been **replaced by verified article-level text** (v4 swapped in the 132 laws that had a clean local source; v5 added the remaining 104); v6 then swapped the criminal law main text for the **consolidated version covering amendments I–XII** (53 “之N” articles such as drunk driving and assisting cybercrime become retrievable for the first time) and filled the **7 missing laws** surfaced by the isolated external question bank (social insurance law, work-injury insurance regulation, copyright law, patent law, environmental protection law, tax collection law, consumer protection law — main texts), so **the corpus reached v6** (v7 is a later pure append over v6 — see the corpus section — and changes no number below). Changing the retrieval unit moves R@5 on the same questions: 34.2% on v4 → 47.4% on v5 → 52.6% on v6 (each measured against its own migrated gold; the last step also includes gold realignment and the fusion-weight retune below). The table below reports the current lexical scope, identical on v6 and v7 (v7 is a pure append); **v1–v5 are historical scopes** (see the comparison below) and must not be mixed with it. Per-law provenance and verification: [docs/article-level-rebuild.md](docs/article-level-rebuild.md).

| Retriever | Synthetic gold Recall@5 | Synthetic MRR | Real-question gold R@5 | Real-question MRR |
|---|---|---|---|---|
| like (substring baseline) | 96.6% | 0.966 | 0.0% | 0.000 |
| bm25 (character bigram) | 98.9% | 0.954 | 44.7% | 0.225 |
| **hybrid (v0.1.1: + weighted synonym-expansion channel)** | **100.0%** | **1.000** | **52.6% (20/38)** | **0.284** |

> **Noise caveat**: with a 38-question gold set one question is worth 2.6pt, so the real R@5 of 52.6% is 20/38; the robust cross-k claim is the deep-pool R@30 of 94.7% (36/38).
> **The synthetic 100.0% is by design, not scoreboard polish**: the v6 synthetic gold is regenerated from the v6 corpus (`make_gold` picks a distinctive in-article phrase as the query); on a clean article-level corpus “distinctive phrase → its article” is the LIKE channel's home turf — this set guards against catastrophic breakage (corpus pollution, broken index) and no longer discriminates fine regressions. Fine-grained discrimination comes from the real-question and blind-bank gold sets below.
> **External isolated question bank (blind-written, round 1)**: 100 statute-retrieval questions written by an agent that could not see the corpus or the code (every question cross-checked against online/local statute texts, 0 unverified); hybrid **R@5 70.0%, R@10 80.0%, R@30 90.0%, MRR 0.584**. It is the validate-only holdout for the **lexical baseline** (see [docs/retrieval-v6-retune.md](docs/retrieval-v6-retune.md) §2.3). 100/100 questions map onto the v6 corpus (89/100 on v5 — the gap is exactly what v6 fills). **Caveat for the semantic layer**: the v7 fusion weight was selected on this very set, and an audit later found 32/100 of its questions echo their target article almost verbatim — so the v7 score on it is in-sample. The held-out re-measurement is in the semantic-rerank subsection below.

**Optional semantic reranking layer (v7, optional dependencies, 2026-10)** — on top of the frozen lexical baseline, same corpus and gold sets:

| Gold set | hybrid baseline R@5 | + v7 R@5 | R@30 |
|---|---|---|---|
| Real-question 38 (used to tune v7) | 52.6% | 71.1% (27/38) | 97.4% |
| Blind bank, round 1, 100 q (used to tune v7) | 70.0% | 89.0% (89/100) | 95.0% |
| **Blind bank, round 2, 100 q (held out — never tuned on)** | **33.0%** | **66.0% (66/100)** | 89.0% |
| Synthetic 177 | 100.0% | 99.4% | 99.4% |

> Round 2 was written under hard isolation: four independent authors, unaware of each other and forbidden from reading anything in this repo or the user's home directory, each writing 25 questions as natural-language paraphrases (no reuse of 4+-character runs from article text). Audit: round-1 questions share ≥8 consecutive characters with their target article in **32/100** cases (median 6, max 24 — near-verbatim); round 2: **0/100** (median 2, max 5). On the same measuring stick the lexical baseline scores 96.9% on round-1's near-verbatim questions but only 29.0% on its genuine paraphrases; under v7 the same split is 96.9% vs 77.4%; after the v2 synonym dictionary the round-1 overall was re-measured at 89.0% (89/100, −1 question — recorded in [docs/metrics.json](docs/metrics.json)). **Blind numbers free of tuning contamination: 66.0% (66/100, corpus v7, pure local pipeline) and 92.0% with the optional LLM reranking layer (below).** The 5 questions whose cited laws were missing from the v6 corpus drove a pure-append corpus fill (6 laws / 361 articles → corpus v7 = 25,987 / 444), so round 2 now scores on all 100; on the v6 corpus it was 64.2% (61/95) and 61.0% if the unmapped five count as misses. Two gold entries were corrected post-audit for a version mismatch (anti-unfair-competition law: the corpus holds the 2025 revision), with audit fields retained in the qbank. Two-round comparison and the per-question failure list: [docs/retrieval-v7-semantic.md](docs/retrieval-v7-semantic.md) §7.
> **One systematic optimization round was run against the 90% target with local algorithms only and stopped there**: 15 algorithm variants (cross depth and weight, promote-only cross, min-rank fusion, semantic-first, rank aggregation, pool depth, query variants, model combinations, a larger reranker, two extra embedding models) were all neutral or harmful; the diagnosis is that the bottleneck is the ranker's ability to tell "semantic neighbour" from "the actual answer" — 91 of 95 golds are already inside the recall pool. The 90% line was ultimately crossed by the **optional LLM reranking layer** (below). Full record, negative results and the three remaining paths: [docs/retrieval-v8-optimization.md](docs/retrieval-v8-optimization.md).
> **The cross-encoder's ledger**: a net win on written questions (p31 +6.4pt, p68 +5.9pt, round 2 +7 questions) and a net loss on real colloquial questions (real 38: 71.1% with it, 81.6% without). It is kept because the target metric is the written bank; a product dominated by real user questions should switch it off (`--no-cross`).

**Optional LLM reranking layer (2026-10-04)** — after the semantic reranker, the pipeline's top-50 candidates go to an LLM that picks the (up to) 5 best-matching articles and promotes them. Reorder-only (no invented entries); on any API failure the layer degrades to the original order; off by default (explicit API key required):

| v8 held-out blind bank, 100 q | R@5 | R@1 | Note |
|---|---|---|---|
| Local pipeline (lexical + semantic + cross-encoder) | 66.0% (66/100) | 33.0% | row 3 above |
| + longcat-2.5-preview-free (free tier) | **92.0% (92/100)** | 82.0% | fills the top-50 ceiling |
| + deepseek-v4.1-flash (OpenCode plan) | **92.0% (92/100)** | 85.0% | identical to the paid platform reference |
| + deepseek-flash (paid platform reference) | 92.0% (top50) / 93.0% (top100) | 85.0% | top100 rescues 1 more |

Configuration was selected on the tuning sets (p31/real38) only; the v8 holdout was measured once per configuration. temperature 0 single measurements carry ±2-question noise and are not bit-reproducible (model and date recorded). 92% is the candidate-depth ceiling: 8 golds sit outside the top-50 (7 outside the top-100), all colloquial paraphrases of abstract rules — a **recall-side gap**, not a ranking one (the reranker rescued 26/26 reachable questions, demoted none). Mechanism, configuration variables, honesty notes and reproduction: [docs/retrieval-llm-rerank.md](docs/retrieval-llm-rerank.md).

**Jev reranking layer (optional, 2026-10-04)** — the same slot as the layer above, but the judge is **Jev** (TypeSafe System One, OpenCode Zen `POST /zen/v1/systemone`): instead of asking a chat model to *generate* a JSON index array, Jev returns a **typed judgement per candidate** (`choice` probability / `score` / `noul`), and `statute_rag/jev_rerank.py` turns that into a ranking — one `choice` question over all 50 candidates (default) or one `score` question per candidate. Same contract as `llm_rerank`: reorder-only, promote at most `pick`, degrade to the original order on any failure, stdlib-only, kept off the core import chain. Measured on the **same v8 holdout and the same candidate pool** as the rows above:

| Judge (all on the local pipeline's top-50) | R@5 | R@1 | rescued / demoted | p50 latency | cost per 100 questions |
|---|---|---|---|---|---|
| + longcat-2.5-preview-free (chat) | 92.0% | 82.0% | 26 / 0 | 3614 ms (p95 23 s) | $0 (free tier) |
| + deepseek-v4.1-flash (chat) | 92.0% | 85.0% | — | 2750 ms | inside the Go plan |
| + **Jev choice, paid `jev-1.13`** | **92.0%** | **84.0%** | 26 / 0 | **1438 ms** | **$0.0368** |
| + Jev score, paid `jev-1.13` | 92.0% | 82.0% | 26 / 0 | 1642 ms | $0.0492 |

Jev reaches the same 92% top-50 ceiling as the chat judges with a far better latency tail (p95 2.4–2.9 s vs longcat's 23 s) at roughly $0.00037 per question, and a dedicated probe measured a **1278 ms p50 for one choice call** and **64 concurrent calls on a single key with zero failures and zero 429**. Honest caveats: the free tier `jev-1.13-free` rate-limits under sustained use (measured 429 `FreeUsageLimitError`, degrading 62–100 of 100 calls), so production should use the paid model; the web UI now shows each result's Jev score. Mechanism, cost/latency tables, the choice-vs-score trade-off, failure analysis and reproduction: [docs/retrieval-jev-rerank.md](docs/retrieval-jev-rerank.md).

**Deep-recall curve** (one fixed depth-30 ranking, truncated at each k — comparable across k): hybrid on the real-question gold reaches **Recall@10 68.4% → Recall@20 86.8% → Recall@30 94.7%**. Only 2 of the 38 questions still miss top-30; 16 sit between ranks 6 and 30 (the reranking working surface the v7 and LLM/Jev layers act on). Full per-k tables and rank histograms: [docs/eval_report.md](docs/eval_report.md) — generated by `scripts/gen_eval_report.py`, with [docs/metrics.json](docs/metrics.json) as the single source of numbers (CI checks both READMEs against it).

**Successive-corpus comparison (current + historical scopes — do not mix with the table above)**:

| Corpus | Articles | hybrid real R@5 | Real MRR@5 | Real R@10 | Real R@30 | Synthetic R@5 |
|---|---|---|---|---|---|---|
| **v7 (current)** | 25,987 | **52.6%** | 0.284 | 68.4% | 94.7% | 100.0% |
| v6 (historical) | 25,626 | **52.6%** | 0.284 | 68.4% | 94.7% | 100.0% |
| v5 (historical) | 25,033 | 47.4% | 0.271 | 65.8% | 94.7% | 86.4% |
| v4 (historical) | 25,551 | 34.2% | 0.242 | 52.6% | 81.6% | 94.9% |
| v3 (historical) | 25,273 | 44.7% | 0.250 | 52.6% | 81.6% | 97.2% |
| v2 (historical) | 16,523 | 39.5% | 0.227 | 52.6% | 81.6% | 98.3% |
| v1 (historical) | 14,212 | 44.7% | 0.266 | 55.3% | 81.6% | 98.9% |

> v1–v3 share one v1-scoped gold (v1 rows survive untouched inside v3, so the comparison holds); v4/v5 replaced the v1 page-block rows, whose ids no longer exist, so each uses its own migrated gold and **those rows are not directly comparable with the v1–v3 rows**.

## The problem

Legal QA / compliance scenarios do not ask retrieval to "find a related document". They require:

- given a statement or a keyword, **hit the exact article**;
- every result **must carry a verifiable source** (law name + article number + original text) — citing the wrong article is worse than answering "not found";
- retrieval quality must come with **reproducible numbers**, not "looks about right".

This project grew out of practice with [legal-wisdom-app](https://github.com/1438388098-glitch/legal-wisdom-app): under unicode61 tokenization, its SQLite FTS5 search degrades Chinese queries into substring/fuzzy matching (empirically tested, see `tests/test_search.py` in that repo) — no relevance scoring, no ranking. statute-rag builds an evaluation-backed retrieval base from zero.

## How it works

```
legal.db ──importer──> corpus JSONL (three-layer quality gate)
              │            · drops (cid:xx) font-mapping debris
              │            · drops <30-char parse residue / empty content
              │            · 14,344 → 14,212 articles (0.9% discarded, v1 scope)
              │              → 25,273 (2026-10 source fill) → 25,033 (article-level
              │                rebuild) → 25,626 (v6: consolidated criminal law +
              │                7 missing laws) → 25,987 (v7: pure 6-law append)
              ▼
   Retrieval trio (unified Citation output: law + article no. + text + id)
   ├── LikeRetriever   exact substring baseline (mimics what unicode61
   │                   actually does to Chinese queries)
   ├── BM25Retriever   character-bigram BM25, inverted postings
   │                   (zero-dependency, no tokenizer)
   └── HybridRetriever multi-channel RRF: original-query BM25
                       + synonym-expanded-query BM25 (synonyms.json,
                         367 colloquial↔statutory entries + numeral
                         normalization, via query_expansion.py)
                       + LIKE (original query)
              ▼  optional layers, off by default (each degrades to the layer above)
   ├── semantic_rerank.py  v7: two embedding models (rank ensemble) → semantic
   │                       top-50 into the recall pool → weighted RRF →
   │                       cross-encoder name-RRF blend on the top-10
   ├── llm_rerank.py       a chat model listwise-picks 5 of the top-50 (promote-only)
   └── jev_rerank.py       Jev typed judgement (choice / score) over the top-50
              ▼
   Evaluation (gold.py + eval_harness.py)
   · Gold: the highest-IDF 6-char phrase that is unique corpus-wide
     → keyword query, fixed seed
   · Metrics: Recall@5 / MRR / multi-k curves / rank histograms
```

Repository layout:

```
statute_rag/          core package (stdlib only)
  ├── importer.py         legal.db → article-level JSONL + quality gate
  ├── retrieval.py        Like / BM25 (inverted postings) / Hybrid RRF, depth–k decoupled
  ├── query_expansion.py  colloquial↔statutory dictionary + numeral normalization
  ├── gold.py             synthetic gold generation (unique-phrase constraint)
  ├── eval_harness.py     Recall@k / MRR / multi-k / rank histograms
  ├── interfaces.py       Reranker contract (the v7 semantic layer plugs in here)
  ├── semantic_rerank.py  optional v7 semantic layer (lazy numpy/transformers, off the core import chain)
  ├── llm_rerank.py       optional LLM listwise rerank layer (stdlib urllib, env-configured, off by default)
  └── jev_rerank.py       optional Jev typed-judgement rerank layer (same slot and contract)
scripts/              CLIs and the web server: run_eval, search_cli, app (UI), bench,
                      gen_eval_report, check_doc_numbers, make_demo_corpus, ablate_retrieval, …
app/index.html        Single-page UI (no build, no external assets, works offline)
tests/                237 unit tests (unittest, no fixtures beyond tmpdirs)
gold/                 committed gold sets + human-review status
docs/                 generated eval report, metrics.json, experiment logs
```

Key design decisions:

- **Unique-phrase constraint**: the first gold design took each article's "highest-IDF phrase"; in testing, 74% of such query phrases were reused across articles (statutes are formulaic), which distorted the recall ceiling. Switching to a "unique corpus-wide" constraint (exact df==1 counted via inverted-index intersection) leaves every question with exactly one correct answer.
- **Prefer importing less over importing bad**: before the article-level rebuild, two-column PDF parsing produced interleaved-column pollution (see "Known failure cases"); the quality gate filters only deterministically identifiable corruption and cannot detect disorder. v5 therefore **re-sources instead of guessing at the broken layout** (official docx / a public statute-text mirror / official gov.cn pages / the legal.db text re-split by line), and leaves a statute out entirely when no clean full text can be obtained (one such law remains).
- **Query expansion adds, never replaces (v0.1.1)**: real questions are colloquial (坐牢 "doing time", 社保 "social insurance") while statutes use formal wording (服刑, 社会保险) — no lexical overlap. The fix is a data-driven domain dictionary with append-style expansion and RRF fusion: **the original query is kept verbatim**, expansion becomes an extra channel (omitted automatically when nothing fires). Anti-overfitting discipline and the per-round ablation (including mechanisms removed for zero gain) are documented in [docs/retrieval-improvement.md](docs/retrieval-improvement.md).
- **Depth–k decoupling**: channel depth is a retrieval configuration, not a return count. `search(k)` uses one fixed depth (the published numbers' configuration); cross-k comparisons come from `recall(query, depth)` — one ranking, truncated at each k (`evaluate_multi_k`). Measured on the real gold (current v7 scope), Recall@5 52.6% → Recall@30 94.7%: **most real questions are already retrieved, they lose on ranking** — the quantified case for the reranking channel, which v7 and the optional LLM/Jev layers now fill.

## Scope (what this is not, stated up front)

- **The core is lexical on purpose; the semantic work is layered on top**: no embeddings and no vector store in the core — character-bigram BM25 is lexical retrieval, zero dependencies, runnable anywhere. That baseline is what every published lexical number measures. The semantic channel is not a roadmap promise any more but a shipped optional layer (v7), and the LLM/Jev rerank layers sit above it; all three are off by default and keep their dependencies out of the core. The lexical bridge (colloquial↔statutory synonym dictionary + query expansion: 坐牢→服刑, 社保→社会保险, 1000元→一千元) lifted real-question Recall@5 from 26.3% to 44.7% (v1 scope, 14,212 articles); purely semantic paraphrases still need the optional layers — see the numbers section.
- **Four gold sets**, each with a stated role: synthetic v6 (177 questions, lexical ceiling), real-question v6 (38 questions, LLM-verified with human legal review still in progress), blind bank round 1 (100 questions, the set the v7 fusion weight was tuned on), blind bank round 2 / v8 (100 questions, written under hard isolation and **never tuned on** — the honest holdout). Details below; [docs/real-question-eval.md](docs/real-question-eval.md).
- **The corpus is not distributed**: statutes come from a local legal-wisdom database (legal.db: 257 laws / 14,344 articles) plus, since 2026-10, 11,061 articles pulled from official/npc sources and a bar-exam statute compilation. v5 then replaced all 14,212 v1 page-block rows with 13,965 article-level rows: 7,340 from official docx files, 6,401 from a public statute-text mirror, 187 from official gov.cn pages and 37 re-split from the legal.db text itself (page-block ratio, not guesswork). The current scope is **25,987 article-level rows covering 444 laws** (v1 was 14,212 / 238, and 61% of those v1 rows were gazette page blocks, not articles). **v7 is a pure append** on top of v6: the 6 laws surfaced by the second-round isolated question bank (trademark law, price law, road-traffic-safety law, paid-annual-leave regulation, juvenile-crime-prevention law, domestic-violence law — 361 articles, same mirror gates), lifting that bank's testable rate from 95/100 to 100/100 while leaving every lexical score on the three older gold sets bit-for-bit identical (see [docs/retrieval-v8-optimization.md](docs/retrieval-v8-optimization.md) §1). The repo contains code, tests, and evaluation artifacts only — reproduction tiers above.
- Nothing here is legal advice; the authoritative text of any statute is its official publication.

## Evaluation

Four gold sets, each with a stated role — the two lexical ones are what the reproduction commands below recompute:

- **Synthetic gold** (seed=20260918, N=177): questions mechanically derived from phrases unique corpus-wide — the lexical recall ceiling of "given a distinctive phrasing, retrieve the article back". Gold: [gold/gold_synth_v6_seed20260918.jsonl](gold/gold_synth_v6_seed20260918.jsonl). It doubles as the **holdout guard** for improvements: any real-gold gain is valid only if the synthetic gold drops ≤ 2pt (observed regression so far: 0). The dictionary's trigger rate on it is 0.6% (1/177) — a natural sentinel against colloquial-dictionary overfitting.
- **Real-question gold v1** (LLM-verified; human legal review in progress): 38 questions taken verbatim from genuine legal Q&A posts on Baidu Zhidao (source URL recorded per question); the raw colloquial question is sent to the retriever unchanged. v0.1 baseline hybrid Recall@5 was **26.3%**; v0.1.1 (v1 scope) lifted it to **44.7%** via the synonym dictionary + query expansion. On the v3 scope it first fell to 31.6% and, after the retrieval recalibration, was back at 44.7%; on v5 (page blocks replaced by article-level text) it is **47.4%** (18/38, each scope against its own migrated gold); on v6 it is **52.6%** (20/38) — 9 stale gold entries were realigned to the in-force corpus text (renumbered 治安管理处罚法 2025 / civil procedure law 2023 / labor dispute interpretation II 2025 had left old article numbers and evidence strings dangling; located by the [scripts/check_gold.py](scripts/check_gold.py) structural gate, realigned by [scripts/fix_gold_real38_stale.py](scripts/fix_gold_real38_stale.py), each row carrying a gold_fix audit field), and the expansion-channel weight was retuned for the v6 corpus (2.5 → 1.5, see [docs/retrieval-v6-retune.md](docs/retrieval-v6-retune.md)). The remaining misses are listed as-is. Per-question details: [docs/real-question-eval.md](docs/real-question-eval.md); gold: [gold/gold_real_38_v6.jsonl](gold/gold_real_38_v6.jsonl); human-review worksheet: [docs/gold-review-worksheet.md](docs/gold-review-worksheet.md).
- **Blind bank, round 1** (100 questions): written by an agent that could not see the corpus or the code, each question cross-checked against online/local statute texts (0 unverified). **The v7 semantic layer's fusion weight was tuned on this set**, and a later audit found 32/100 of its questions echo their target article almost verbatim (median 6, max 24 characters) — so its lexical 70.0% / semantic 89.0% are in-sample and kept for comparison only. Gold: [gold/gold_external_v6.jsonl](gold/gold_external_v6.jsonl).
- **Blind bank, round 2 / v8 holdout** (100 questions): four independent authors, unaware of each other and forbidden from reading anything in this repo or the user's home directory, each writing 25 natural-language paraphrases (no reuse of 4+-character runs from article text). **Never tuned on** — the only blind number here free of tuning contamination: hybrid 33.0%, + v7 semantic 66.0%, + optional LLM/Jev rerank 92.0%, all on corpus v7 with 100/100 testable. Gold: [gold/gold_blind_v8_v7.jsonl](gold/gold_blind_v8_v7.jsonl); question bank: [gold/qbank_blind_v8.jsonl](gold/qbank_blind_v8.jsonl).

Reproducing — three honest tiers:

1. **No corpus (anyone)**: the demo path above verifies the mechanism end to end (CI asserts it on every push).
2. **Same-source corpus (legal-wisdom legal.db, same version)**: full reproduction of the two lexical gold numbers (synthetic 100.0%, real-question 52.6%; v7 is a pure append over v6, so the numbers are identical on either):

   ```bash
   # synthetic gold on the current corpus
   python scripts/run_eval.py --corpus data/corpus_v7.jsonl --gold gold/gold_synth_v6_seed20260918.jsonl --out-dir data
   # real-question gold
   python scripts/run_eval.py --corpus data/corpus_v7.jsonl --gold gold/gold_real_38_v6.jsonl --out-dir data --gold-desc "real-question gold v6"
   # Ablation: baseline vs improved config on both gold sets (multi-k)
   python scripts/ablate_retrieval.py --corpus data/corpus_v7.jsonl \
       --gold-real gold/gold_real_38_v6.jsonl --gold-synth gold/gold_synth_v6_seed20260918.jsonl \
       --ks 5,10,20,30
   ```

   The blind-bank, semantic and LLM/Jev numbers additionally need the optional layers (precomputed offline article embeddings, plus an API key for the LLM/Jev judges); their reproduction commands are in [docs/retrieval-v7-semantic.md](docs/retrieval-v7-semantic.md) §7, [docs/retrieval-llm-rerank.md](docs/retrieval-llm-rerank.md) and [docs/retrieval-jev-rerank.md](docs/retrieval-jev-rerank.md).

3. **Your own Chinese statute corpus**: the retrieval trio, synthetic gold and quality gate work out of the box; the committed `gold_real_38_v6.jsonl` binds `gold_id`s to our import's chunk-row ids, so its numbers cannot be re-run on a different corpus — reuse the 38 questions by rebuilding row ids with `build_real_gold.py` (questions and source URLs carry over).

- Unit tests: `python -m unittest discover -s tests` (237 cases). Gold sanity gate: `python scripts/check_gold.py --corpus data/corpus_v7.jsonl --gold gold/gold_real_38_v6.jsonl --gold gold/gold_external_v6.jsonl --gold gold/gold_blind_v8_v7.jsonl --gold gold/gold_synth_v6_seed20260918.jsonl`. Latency reference: `python scripts/bench.py` (machine-relative, for before/after comparisons only).

## Known failure cases

- **Out-of-order article pollution (fixed in v5; one law still unsourced)**: querying 正当防卫 ("justifiable defense") once hit an article of the《突发公共卫生事件应对法》 — that PDF was parsed column by column into interleaved lines, then chunked by length into ~90%-overlapping “page blocks”; 61% of the v1 rows were such blocks (median 10 articles inside one 1,400-character row). The quality gate cannot detect that disorder, so v5 **re-sources rather than guessing at the layout**: all 14,212 v1 page-block rows were replaced with 13,965 verified article-level rows and the length-based chunking step was removed at the entry point (per-law provenance, version dates and gates in [docs/article-level-rebuild.md](docs/article-level-rebuild.md)). Gates are hard: article numbers must run 1..N with no gap or regression, every article must start with its number and end on sentence punctuation without gazette furniture, and otherwise the whole law is rejected. Only one law is left out — the SPC reply on advance payment from the basic medical-insurance fund, whose gazette page interleaves the public-notice block with two-column body text and yields no clean full text from any source; **missing beats wrong**, and it is recorded in the gap list.
- **The lexical ceiling, and how the layers moved it (and where the remaining gap sits)**: on the frozen lexical baseline the real-question misses are dominated by pure semantic equivalence (paraphrases with no dictionary bridge), intra-/cross-law competition among similar articles, and extraction pollution — exactly what the v7 semantic layer and the LLM/Jev layers address: real-question R@5 goes 52.6% → 71.1% (semantic) and the blind v8 holdout 33.0% → 66.0% → 92.0% (+ LLM/Jev). What remains is now a **recall-side gap on the blind bank**: 8 golds sit outside the top-50 candidates (7 outside the top-100), all colloquial paraphrases of abstract rules ("不用马上进监狱" vs 缓刑/暂予监外执行) that share almost no characters with the article. No reranker can recover them; closing it needs chapter/section titles in the indexed text or a stronger legal-domain model (see [docs/retrieval-llm-rerank.md](docs/retrieval-llm-rerank.md) §4). The earlier BM25/weight tuning history (b 0.75→0.6 and expansion weight 1→2.5 for the v3 page-block mix, later retuned to 1.5 for the clean v6 article-level corpus) is historical; grids and negative results in [docs/retrieval-v3-diagnosis.md](docs/retrieval-v3-diagnosis.md) §5–6 and [docs/retrieval-v6-retune.md](docs/retrieval-v6-retune.md).
- **Synthetic gold is conservative**: it measures lexical recall of "distinctive phrasing → article" — saturated at 100.0% on the v6 regenerated gold (by design; see the numbers section), while real-question recall is far lower (52.6%) — both numbers are reported for exactly this reason. On the v5 migrated gold the same mechanism measured 86.4%, mostly gold staleness: queries came from the replaced rows.

## FAQ

- **Why is the core lexical, with no embeddings?** Character-bigram BM25 is deliberately lexical: zero dependencies, no tokenizer, no model — the point is an evaluation baseline you can run anywhere. Embeddings and rerankers are not a roadmap promise any more but shipped optional layers on the frozen `Reranker` interface ([statute_rag/interfaces.py](statute_rag/interfaces.py)): the v7 semantic layer ([statute_rag/semantic_rerank.py](statute_rag/semantic_rerank.py)) plus the LLM/Jev rerank layers ([statute_rag/llm_rerank.py](statute_rag/llm_rerank.py), [statute_rag/jev_rerank.py](statute_rag/jev_rerank.py)). They are off by default and their dependencies never enter the core package.
- **Can I use your corpus?** No — it is not distributed (source: local legal-wisdom database). The code, gold schema and evaluation protocol are the reusable part; see the three reproduction tiers above.
- **Why is like 0.0% on real questions?** Real questions are colloquial sentences that never appear verbatim inside statutes; substring matching has nothing to match. See [docs/real-question-eval.md](docs/real-question-eval.md) §5.
- **How do I check the numbers?** [docs/metrics.json](docs/metrics.json) is the single source; `check_doc_numbers.py` (CI) verifies both READMEs match it, and every number is regenerable from the commands above.

## Roadmap

- **v0.2 (shipped, 2026-10)**: the semantic reranking channel on the frozen `Reranker` interface landed (v7, local, optional dependency), together with the optional LLM and Jev rerank layers above it. Measured on the holdout: real-question R@5 52.6% → 71.1%, blind v8 33.0% → 66.0% → 92.0% with the LLM/Jev layer. Remaining v0.2 work: real-question gold expansion (38 → 200 questions) + human legal review, and closing the blind-bank recall gap (the 8 golds outside the top-50) by indexing chapter/section titles rather than adding more rerankers. The four pre-2025 治安管理处罚法 questions were re-authored against the in-force text in v6 (gold_fix audit fields).
- **v0.3**: article/clause/item multi-level chunking; statute version alignment (temporal validity); a "refuse to answer when retrieval fails" policy and hallucination-guardrail evaluation.

## Versioning & citation

See [CHANGELOG.md](CHANGELOG.md) for the version history (Keep a Changelog format). To cite this project, use the metadata in [CITATION.cff](CITATION.cff) (GitHub renders it as a "Cite this repository" button).

## License

[MIT](LICENSE)
