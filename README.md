English · [简体中文](./README.zh-CN.md)

[![CI](https://github.com/1438388098-glitch/statute-rag/actions/workflows/ci.yml/badge.svg)](https://github.com/1438388098-glitch/statute-rag/actions/workflows/ci.yml)

# statute-rag · Article-level hybrid statute retrieval

A statute-retrieval pipeline that treats the **article** as the retrieval unit and **verifiable citations** as a hard constraint, with a **fully reproducible offline evaluation**. Structured article-level chunking → character-bigram BM25 (original + synonym-expanded query) / LIKE multi-channel RRF fusion → forced article-level citations.

**Version: v0.1.1 tag; the main branch adds a measurement foundation (depth/k decoupling, Recall@k curves), a frozen Reranker interface, and pip-installable packaging on top — see [CHANGELOG.md](CHANGELOG.md). A semantic vector channel is on the roadmap and is not part of what any version claims.**

## Quick start (no corpus required, ~30 seconds)

Pure Python standard library, zero third-party runtime dependencies. Python ≥ 3.8 (CI runs 3.9 and 3.13).

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
python scripts/app.py                                      # reads data/corpus_v3.jsonl
python scripts/app.py --corpus demo_corpus/corpus.jsonl    # demo corpus if you have no real one
```

Starts a local server (default `http://127.0.0.1:8787/`) and opens a browser. The UI is a search box plus result cards: law name, article number, full text, with the matched fragments underlined in blue. Every result also shows **which channels hit it** (literal / expanded / exact-substring), and when a colloquial query was rewritten by the dictionary the actual search string is displayed — so "why did this article come back" is visible rather than a black box.

Three rules hold this together:

- **The UI and the evaluation share one `HybridRetriever` instance and one fused ranking.** The hit-source trace comes from `HybridRetriever.recall_with_trace()` — read-only, never scored; `recall` is literally implemented on top of it (bit-for-bit identical). So "the ranking the UI shows is the ranking we evaluate" is a structural fact, not a promise, and there is no separate demo logic to drift.
- The frontend is a single file, [app/index.html](app/index.html): **no build step, no npm, no external scripts or webfonts**, works offline.
- The server binds `127.0.0.1` only and maps no filesystem paths (just the embedded page plus two JSON endpoints, `/api/meta` and `/api/search`), so there is no directory-traversal surface. Exposing it to a LAN or the internet requires an explicit `--host` plus your own reverse proxy — not something this project does by default.

> The corpus is not distributed with the repo, so run the app **where the corpus lives**. Every number the UI shows (article/law counts, index build time, dictionary size) is read from the corpus and code, never hardcoded.

## The numbers (current scope: v6 corpus, 25,626 articles / 438 laws; three gold sets side by side)

> **Scope-change notice**: since 2026-10 all 14,212 v1 “gazette page-block” rows have been **replaced by verified article-level text** (v4 swapped in the 132 laws that had a clean local source; v5 added the remaining 104); v6 then swapped the criminal law main text for the **consolidated version covering amendments I–XII** (53 “之N” articles such as drunk driving and assisting cybercrime become retrievable for the first time) and filled the **7 missing laws** surfaced by the isolated external question bank (social insurance law, work-injury insurance regulation, copyright law, patent law, environmental protection law, tax collection law, consumer protection law — main texts), so **the current scope is the v6 corpus**. Changing the retrieval unit moves R@5 on the same questions: 34.2% on v4 → 47.4% on v5 → 52.6% on v6 (each measured against its own migrated gold; the last step also includes gold realignment and the fusion-weight retune below). The table below is the v6 current scope; **v1–v5 are historical scopes** (see the comparison below) and must not be mixed with it. Per-law provenance and verification: [docs/article-level-rebuild.md](docs/article-level-rebuild.md).

| Retriever | Synthetic gold Recall@5 | Synthetic MRR | Real-question gold R@5 | Real-question MRR |
|---|---|---|---|---|
| like (substring baseline) | 96.6% | 0.966 | 0.0% | 0.000 |
| bm25 (character bigram) | 98.9% | 0.954 | 44.7% | 0.225 |
| **hybrid (v0.1.1: + weighted synonym-expansion channel)** | **100.0%** | **1.000** | **52.6% (20/38)** | **0.284** |

> **Noise caveat**: with a 38-question gold set one question is worth 2.6pt, so the real R@5 of 52.6% is 20/38; the robust cross-k claim is the deep-pool R@30 of 94.7% (36/38).
> **The synthetic 100.0% is by design, not scoreboard polish**: the v6 synthetic gold is regenerated from the v6 corpus (`make_gold` picks a distinctive in-article phrase as the query); on a clean article-level corpus “distinctive phrase → its article” is the LIKE channel's home turf — this set guards against catastrophic breakage (corpus pollution, broken index) and no longer discriminates fine regressions. Fine-grained discrimination comes from the two gold sets below.
> **External isolated question bank (blind-written, never used for tuning)**: 100 statute-retrieval questions written by an agent that could not see the corpus or the code (every question cross-checked against online/local statute texts, 0 unverified); hybrid **R@5 70.0%, R@10 80.0%, R@30 90.0%, MRR 0.584**. It is this repo's validate-only holdout (see [docs/retrieval-v6-retune.md](docs/retrieval-v6-retune.md) §2.3). 100/100 questions map onto the v6 corpus (89/100 on v5 — the gap is exactly what v6 fills).

**Deep-recall curve** (one fixed depth-30 ranking, truncated at each k — comparable across k): hybrid on the real-question gold reaches **Recall@10 68.4% → Recall@20 86.8% → Recall@30 94.7%**. Only 2 of the 38 questions still miss top-30; 16 sit between ranks 6 and 30 (the reranking working surface for v0.2). Full per-k tables and rank histograms: [docs/eval_report.md](docs/eval_report.md) — generated by `scripts/gen_eval_report.py`, with [docs/metrics.json](docs/metrics.json) as the single source of numbers (CI checks both READMEs against it).

**Successive-corpus comparison (current + historical scopes — do not mix with the table above)**:

| Corpus | Articles | hybrid real R@5 | Real MRR@5 | Real R@10 | Real R@30 | Synthetic R@5 |
|---|---|---|---|---|---|---|
| **v6 (current)** | 25,626 | **52.6%** | 0.284 | 68.4% | 94.7% | 100.0% |
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
              │            · 14,344 → 14,212 articles (0.9% discarded)
              ▼
   Retrieval trio (unified Citation output: law + article no. + text + id)
   ├── LikeRetriever   exact substring baseline (mimics what unicode61
   │                   actually does to Chinese queries)
   ├── BM25Retriever   character-bigram BM25, inverted postings
   │                   (zero-dependency, no tokenizer)
   └── HybridRetriever multi-channel RRF: original-query BM25
                       + synonym-expanded-query BM25 (synonyms.json,
                         127 colloquial↔statutory entries + numeral
                         normalization, via query_expansion.py)
                       + LIKE (original query)
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
  └── interfaces.py       Reranker contract for v0.2 (model plugs in later)
scripts/              CLIs and the web server: run_eval, search_cli, app (UI), bench,
                      gen_eval_report, check_doc_numbers, make_demo_corpus, ablate_retrieval, …
app/index.html        Single-page UI (no build, no external assets, works offline)
tests/                201 unit tests (unittest, no fixtures beyond tmpdirs)
gold/                 committed gold sets + human-review status
docs/                 generated eval report, metrics.json, experiment logs
```

Key design decisions:

- **Unique-phrase constraint**: the first gold design took each article's "highest-IDF phrase"; in testing, 74% of such query phrases were reused across articles (statutes are formulaic), which distorted the recall ceiling. Switching to a "unique corpus-wide" constraint (exact df==1 counted via inverted-index intersection) leaves every question with exactly one correct answer.
- **Prefer importing less over importing bad**: before the article-level rebuild, two-column PDF parsing produced interleaved-column pollution (see "Known failure cases"); the quality gate filters only deterministically identifiable corruption and cannot detect disorder. v5 therefore **re-sources instead of guessing at the broken layout** (official docx / a public statute-text mirror / official gov.cn pages / the legal.db text re-split by line), and leaves a statute out entirely when no clean full text can be obtained (one such law remains).
- **Query expansion adds, never replaces (v0.1.1)**: real questions are colloquial (坐牢 "doing time", 社保 "social insurance") while statutes use formal wording (服刑, 社会保险) — no lexical overlap. The fix is a data-driven domain dictionary with append-style expansion and RRF fusion: **the original query is kept verbatim**, expansion becomes an extra channel (omitted automatically when nothing fires). Anti-overfitting discipline and the per-round ablation (including mechanisms removed for zero gain) are documented in [docs/retrieval-improvement.md](docs/retrieval-improvement.md).
- **Depth–k decoupling**: channel depth is a retrieval configuration, not a return count. `search(k)` uses one fixed depth (the published numbers' configuration); cross-k comparisons come from `recall(query, depth)` — one ranking, truncated at each k (`evaluate_multi_k`). Measured on the real gold (current v6 scope), Recall@5 52.6% → Recall@30 94.7%: **most real questions are already retrieved, they lose on ranking** — the quantified case for the v0.2 reranking channel.

## Scope (what this is not, stated up front)

- **Not a semantic RAG, for now**: no embeddings, no vector store — character-bigram BM25 is lexical retrieval. v0.1.1 built a lexical bridge (colloquial↔statutory synonym dictionary + query expansion: 坐牢→服刑, 社保→社会保险, 1000元→一千元), lifting real-question Recall@5 from 26.3% to 44.7% (measured on the v1 scope, 14,212 articles); purely semantic paraphrases still cannot be hit. The semantic channel needs a usable Chinese embedding model/API and belongs to v0.2.
- **Two gold sets**: the synthetic gold measures the lexical recall ceiling; the real-question gold v1 (38 questions, LLM-verified, human legal review in progress) measures real-question performance. See [docs/real-question-eval.md](docs/real-question-eval.md).
- **The corpus is not distributed**: statutes come from a local legal-wisdom database (legal.db: 257 laws / 14,344 articles) plus, since 2026-10, 11,061 articles pulled from official/npc sources and a bar-exam statute compilation. v5 then replaced all 14,212 v1 page-block rows with 13,965 article-level rows: 7,340 from official docx files, 6,401 from a public statute-text mirror, 187 from official gov.cn pages and 37 re-split from the legal.db text itself (page-block ratio, not guesswork). The current scope is **25,626 article-level rows covering 438 laws** (v1 was 14,212 / 238, and 61% of those v1 rows were gazette page blocks, not articles). The repo contains code, tests, and evaluation artifacts only — reproduction tiers above.
- Nothing here is legal advice; the authoritative text of any statute is its official publication.

## Evaluation

Two gold sets, two scopes, reported side by side:

- **Synthetic gold** (seed=20260918, N=177): questions mechanically derived from phrases unique corpus-wide — the lexical recall ceiling of "given a distinctive phrasing, retrieve the article back". Gold: [gold/gold_synth_seed20260918.jsonl](gold/gold_synth_seed20260918.jsonl). It doubles as the **holdout guard** for improvements: any real-gold gain is valid only if the synthetic gold drops ≤ 2pt (observed regression so far: 0). The dictionary's trigger rate on it is 0.6% (1/177) — a natural sentinel against colloquial-dictionary overfitting.
- **Real-question gold v1** (LLM-verified; human legal review in progress): 38 questions taken verbatim from genuine legal Q&A posts on Baidu Zhidao (source URL recorded per question); the raw colloquial question is sent to the retriever unchanged. v0.1 baseline hybrid Recall@5 was **26.3%**; v0.1.1 (v1 scope) lifted it to **44.7%** via the synonym dictionary + query expansion. On the v3 scope it first fell to 31.6% and, after the retrieval recalibration, was back at 44.7%; on v5 (page blocks replaced by article-level text) it is **47.4%** (18/38, each scope against its own migrated gold); on v6 it is **52.6%** (20/38) — 9 stale gold entries were realigned to the in-force corpus text (renumbered 治安管理处罚法 2025 / civil procedure law 2023 / labor dispute interpretation II 2025 had left old article numbers and evidence strings dangling; located by the [scripts/check_gold.py](scripts/check_gold.py) structural gate, realigned by [scripts/fix_gold_real38_stale.py](scripts/fix_gold_real38_stale.py), each row carrying a gold_fix audit field), and the expansion-channel weight was retuned for the v6 corpus (2.5 → 1.5, see [docs/retrieval-v6-retune.md](docs/retrieval-v6-retune.md)). The remaining misses are listed as-is. Per-question details: [docs/real-question-eval.md](docs/real-question-eval.md); gold: [gold/gold_real_38_v6.jsonl](gold/gold_real_38_v6.jsonl); human-review worksheet: [docs/gold-review-worksheet.md](docs/gold-review-worksheet.md).

Reproducing — three honest tiers:

1. **No corpus (anyone)**: the demo path above verifies the mechanism end to end (CI asserts it on every push).
2. **Same-source corpus (legal-wisdom legal.db, same version)**: full reproduction of both gold numbers:

   ```bash
   python scripts/run_eval.py --corpus data/corpus_v3.jsonl --gold gold/gold_synth_seed20260918.jsonl --out-dir data
   python scripts/build_real_gold.py --corpus data/corpus_v3.jsonl --out gold/gold_real_38.jsonl
   python scripts/run_eval.py --corpus data/corpus_v3.jsonl --gold gold/gold_real_38.jsonl --out-dir data --gold-desc "real-question gold v1"
   # Ablation: baseline vs improved config on both gold sets (multi-k)
   python scripts/ablate_retrieval.py --corpus data/corpus_v3.jsonl \
       --gold-real gold/gold_real_38.jsonl --gold-synth gold/gold_synth_seed20260918.jsonl \
       --ks 5,10,20,30
   ```

3. **Your own Chinese statute corpus**: the retrieval trio, synthetic gold and quality gate work out of the box; the committed `gold_real_38.jsonl` binds `gold_id`s to our import's chunk-row ids, so its numbers cannot be re-run on a different corpus — reuse the 38 questions by rebuilding row ids with `build_real_gold.py` (questions and source URLs carry over).

- Unit tests: `python -m unittest discover -s tests` (201 cases). Gold sanity gate: `python scripts/check_gold.py --corpus data/corpus_v6.jsonl --gold gold/gold_real_38_v6.jsonl --gold gold/gold_external_v6.jsonl --gold gold/gold_synth_v6_seed20260918.jsonl`. Latency reference: `python scripts/bench.py` (machine-relative, for before/after comparisons only).

## Known failure cases

- **Out-of-order article pollution (fixed in v5; one law still unsourced)**: querying 正当防卫 ("justifiable defense") once hit an article of the《突发公共卫生事件应对法》 — that PDF was parsed column by column into interleaved lines, then chunked by length into ~90%-overlapping “page blocks”; 61% of the v1 rows were such blocks (median 10 articles inside one 1,400-character row). The quality gate cannot detect that disorder, so v5 **re-sources rather than guessing at the layout**: all 14,212 v1 page-block rows were replaced with 13,965 verified article-level rows and the length-based chunking step was removed at the entry point (per-law provenance, version dates and gates in [docs/article-level-rebuild.md](docs/article-level-rebuild.md)). Gates are hard: article numbers must run 1..N with no gap or regression, every article must start with its number and end on sentence punctuation without gazette furniture, and otherwise the whole law is rejected. Only one law is left out — the SPC reply on advance payment from the basic medical-insurance fund, whose gazette page interleaves the public-notice block with two-column body text and yields no clean full text from any source; **missing beats wrong**, and it is recorded in the gap list.
- **The lexical ceiling (partially raised by query expansion)**: the 21 remaining misses are dominated by pure semantic equivalence (paraphrases with no dictionary bridge), intra-/cross-law competition among similar articles, and extraction pollution — these need the v0.2 vector semantic channel + reranking, not a bigger dictionary. Weighted RRF and BM25 k1/b tuning were measured and discarded on the v1 corpus (see negative results in [docs/retrieval-improvement.md](docs/retrieval-improvement.md) §4) — but enlarging the corpus changed that premise: v3 mixes 100-char standalone articles with 1,400-char gazette page blocks, so the BM25 length normalisation (b) and the expansion-channel weight became load-bearing. Recalibrating both for v3 (b 0.75→0.6, expansion weight 1→2.5) is what restored real R@5, at the cost of lower MRR@5 on v1/v2 — full grids and negative results in [docs/retrieval-v3-diagnosis.md](docs/retrieval-v3-diagnosis.md) §5–6.
- **Synthetic gold is conservative**: it measures lexical recall of "distinctive phrasing → article" — saturated at 100.0% on the v6 regenerated gold (by design; see the numbers section), while real-question recall is far lower (52.6%) — both numbers are reported for exactly this reason. On the v5 migrated gold the same mechanism measured 86.4%, mostly gold staleness: queries came from the replaced rows.

## FAQ

- **Why no embeddings?** Character-bigram BM25 is deliberately lexical: zero dependencies, no tokenizer, no model — the point is an evaluation baseline you can run anywhere. The semantic channel is scoped for v0.2 behind the frozen `Reranker` interface ([statute_rag/interfaces.py](statute_rag/interfaces.py)); optional dependencies will stay out of the core.
- **Can I use your corpus?** No — it is not distributed (source: local legal-wisdom database). The code, gold schema and evaluation protocol are the reusable part; see the three reproduction tiers above.
- **Why is like 0.0% on real questions?** Real questions are colloquial sentences that never appear verbatim inside statutes; substring matching has nothing to match. See [docs/real-question-eval.md](docs/real-question-eval.md) §5.
- **How do I check the numbers?** [docs/metrics.json](docs/metrics.json) is the single source; `check_doc_numbers.py` (CI) verifies both READMEs match it, and every number is regenerable from the commands above.

## Roadmap

- **v0.2**: semantic reranking channel on the frozen `Reranker` interface (local/remote pluggable, optional dependency) — the target is quantified: lift real-question Recall@5 (52.6%) toward the Recall@30 ceiling (94.7%); real-question gold expansion (target 200 questions) + human legal review. The four pre-2025 治安管理处罚法 questions were re-authored against the in-force text in v6 (gold_fix audit fields).
- **v0.3**: article/clause/item multi-level chunking; statute version alignment (temporal validity); a "refuse to answer when retrieval fails" policy and hallucination-guardrail evaluation.

## Versioning & citation

See [CHANGELOG.md](CHANGELOG.md) for the version history (Keep a Changelog format). To cite this project, use the metadata in [CITATION.cff](CITATION.cff) (GitHub renders it as a "Cite this repository" button).

## License

[MIT](LICENSE)
