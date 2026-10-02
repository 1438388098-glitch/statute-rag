# Changelog

本项目遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号遵循语义化版本。日期为对应 git tag 的日期。评测数字的单一来源是 [docs/metrics.json](docs/metrics.json)；已实测无增益的机制一律从代码移除并记入负结果清单（[docs/retrieval-improvement.md](docs/retrieval-improvement.md) §4），本日志只列链接不重抄。

## [Unreleased]

### Fixed
- LikeRetriever 多文档命中排序：原实现主键是文档下标降序，与「位置越靠前越相关」的注释语义相反；已改为按命中位置升序（双金标数字零漂移）
- run_eval 在全新输出目录上生成合成金标时因目录不存在崩溃（makedirs 时机早于写入）

### Changed
- BM25 检索索引改为倒排 postings：单查询延迟 p50 43.1→12.7 ms、hybrid 102.7→30.3 ms（3.4×，真实语料基准，口径见 scripts/bench.py）；一次性索引构建 5.7→8.2 s
- 双重审查修复：重排器返回空列表显式报错（契约：可截断不可清空）；bench 解析金标改用 json.loads

### Added
- **度量底座**：HybridRetriever 通道深度与 k 解耦（`recall(query, depth)` 完整融合排名 → 截 k）；`evaluate_multi_k` 同一份排名算 Recall@5/10/20/30；`rank_histogram` 金标排名分布；`scripts/bench.py` 延迟基准。真实问句固定深度 30 口径：R@5 44.7% → R@10 55.3% → R@20 68.4% → R@30 78.9%，21 题未命中中 13 题在 top-30 内（v0.2 重排工作面）
- **Reranker 接口**（`statute_rag/interfaces.py`）：`HybridRetriever(reranker=…)` 接入点，reranker=None 与 v0.1.1 逐位等价；只动顺序不动 Citation 形状，语义依赖不进核心 import 链，真模型适配器留待 v0.2
- **可安装包**：pyproject.toml（零运行时依赖，dynamic version 单点，package-data 收录 synonyms.json），CI 安装冒烟
- **评测报告机器生成**：`scripts/gen_eval_report.py` 产出 [docs/eval_report.md](docs/eval_report.md)（补齐真实问句口径、深度曲线、排名分布）与 [docs/metrics.json](docs/metrics.json)（数字单一来源）；`scripts/check_doc_numbers.py` 在 CI 对账两份 README（15 个展示数字）
- **金标复核闭环**：`gold/real38_review_status.json` 持久化人工复核状态（重生成工作表不丢结果，表头进度计数）；r003/r004/r012/r030 标行标签偏移 ⚠；real-question-eval §7/§10 条号口径矛盾如实标注、待复核裁决
- **工程**：CI 矩阵 3.9/3.13 + ruff 基础 lint 门禁；CLI 友好报错、run_eval 自动建输出目录、search_cli --full
- BM25 k1/b 参数网格与加权 RRF 两条负结果补入负结果清单

## [v0.1.1] - 2026-09-29

### Added
- 真实问句金标 v1：38 条百度知道真实法律提问（逐条记录来源 URL，LLM 核验 + 证据句逐字校验），评测框架支持多金标行（gold_ids，命中任一行即算命中）
- 法律口语↔法言法语同义词典（127 词条）+ 追加式查询扩展（原查询只增不改）+ 数字读法归一（1000元→一千元），hybrid 升级为三路 RRF 融合
- 人工复核工作表（[docs/gold-review-worksheet.md](docs/gold-review-worksheet.md)）
- CI：单测 + demo 评测断言门禁（hybrid Recall@5 == 100%）

### Changed
- **真实问句 Recall@5 26.3% → 44.7%（+18.4pt）**，MRR 0.180 → 0.312；合成金标 98.9% 零回退（留出守门线 ≤2pt）
- 融合通道深度按消融定为 30（更深反降）

## [v0.1.0] - 2026-09-18

### Added
- 条文级导入器与三层质检门：14,344 → 14,212 条（弃 0.9%），过滤 (cid:) 字体映射残片与过短解析残渣
- 检索三件套：LIKE 精确子串基线 / 字符二元组 BM25（零依赖、无分词器）/ RRF 混合融合；统一 Citation 输出（法律名 + 条号 + 原文 + id）
- 合成金标 177 题（全库唯一 6 字短语，固定 seed=20260918）：hybrid Recall@5 98.9%、MRR 0.984
- 30 秒演示 CLI 与合成演示语料（无真实语料即可端到端跑通）
- 两份 README（英文完整版 + 中文版）与四段式文档（问题/边界/机制/验证）
