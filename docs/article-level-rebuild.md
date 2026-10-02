# 语料条级重建（v4）：把「公报页块行」换掉的那一批

> 用户报障原文：**「你现在连法条拆分都没做好」**，并附界面截图 —— 检索结果里
> `id=70879`「最高人民法院关于适用《中华人民共和国民法典》侵权责任编的解释（一）· 第十九条」
> 的正文是**交错乱文**：「因产品存在缺陷造成买受人 动物造成他人损害，动物饲养人或者管理人
> 财产损害，买受人请求产品的生产者或者销 主张不承担责任……」。

## 1. 这不是一行坏了，是一整层坏了（先量化）

对 `data/corpus_v3.jsonl`（25,273 行）实测：

| 事实 | 数 |
|---|---|
| v1 段行数（`meta` 为空且 id < 910000） | **14,212 行 / 238 部法** |
| 其中**内部含 >1 个「第X条」**的行（=不是条，是页块） | **15,416 / 16,523 行 = 61%**（含 v2 段） |
| v1 每行条数 | 中位数 **10**、均值 9.2、最大 67 |
| v1 每行字数 | 中位数 **1,417** |
| v1 相邻行关系 | **重叠约 90% 的滑窗**（≈5.7 倍冗余） |
| 双栏交错（疑似） | 5,010 行（另有 2,193 行窄栏未带排版标记，合计 7,203 行） |

**根因（已定位到代码）**：v1 语料来自 `legal-wisdom-app/data/database/legal.db`（257 部文档），
入库用的 `legal-wisdom-app/data/parsers/pdf_parser.py` 调的是 pdfminer 的
`extract_text(file_path, laparams=...)` —— **不做分栏**。公报 PDF 是双栏排版，pdfminer 按
文本块顺序拼，左右栏就逐行交错；再叠加按长度切块（每块约 1,400 字、步长重叠），于是
「一条一小行」变成了「一面一块、块块相交、块内两栏串行」。

**结论**：项目对外宣称的「以**条**为检索单元」，在最老的这 14,212 行（占全语料 56%）上
**不成立**。用户看到的是真实缺陷，不是显示 bug。

## 2. 修法：换源，不去猜（本轮已落地）

不去做「把交错文本猜回去」的去交错（猜错了无法校验、还会静默产出假条文），而是**换干净源**：

- 干净源：`rag-server/documents/`（163 个文件：148 docx + 15 md，标准法条体例：`第一条 …`、
  章标题 `第一章 …`）。干净度实测：**153/163 的行首条号序列单调**（=干净），
  与库内 257 部法按法名规范化后**交集 140 部**。
- 切分复用已有并有单测的 `scripts/docx_to_articles.py`（条号单调递增防「本法第X条」误切、
  `之N` 后缀、编/章/节标题跳过、零第三方依赖）。
- **版本对照**：源件声明的年份 vs `legal.db` 的公布日期，**源件更旧的一律不用**（本轮 8 部因此拒用）。
- **逐条硬校验**：以条号开头、以句末标点结尾、空格密度不异常（<0.10，交错行的特征）、
  无公报页眉页脚；**逐法条号全序递增**。不过门就丢，不凑数。

本轮结果（`scripts/resource_from_clean_files.py`）：

| 项 | 数 |
|---|---|
| v1 待修（页块为主）的法 | 234 部 |
| **本次重采覆盖** | **132 部 → 产出 7,340 条** |
| 逐法条号单调 | **132 / 132** |
| 丢弃条数 | **0** |
| 源件版本过时（拒用，未覆盖） | 8 部 |
| 无本地干净源（未覆盖） | 94 部 |
| v1 本身已是条级（无需重采） | 4 部 |

## 3. v4 语料（已切换为 app 默认）

`scripts/rebuild_corpus_v4.py` 把上述条级行换进语料，并**按 evidence 逐题校验**迁移金标：

```
python scripts/rebuild_corpus_v4.py --base data/corpus_v3.jsonl \
  --replacement data/flk/fragments/clean_source.jsonl \
  --out data/corpus_v4.jsonl \
  --gold gold/gold_real_38.jsonl --gold-out gold/gold_real_38_v4.jsonl \
  --gold-synth gold/gold_synth_seed20260918.jsonl \
  --gold-synth-out gold/gold_synth_v4_seed20260918.jsonl
```

| 项 | v3 | **v4** |
|---|---|---|
| 行数 | 25,273 | 25,551 |
| 法律部数 | 432 | 432 |
| 被替换掉的 v1 页块行 | — | 7,062 |
| 新增条级行 | — | 7,340 |
| 真实金标 38 题 evidence 逐字复核 | — | **38 / 38 通过** |
| 合成金标 177 题解析到新行 | — | 176 / 177 |

**同口径（同一套迁移后金标）对比 v3 vs v4**，真实问句 hybrid：

| 语料 | R@5 | MRR@5 | R@10 | R@30 |
|---|---|---|---|---|
| v3 | 31.6% | 0.150 | 36.8% | 55.3% |
| **v4** | **34.2%** | **0.242** | **52.6%** | **81.6%** |

合成金标 R@5：v4 = 94.9%。

**必须如实说明的两点口径变化**：
1. 这 130 部法的**检索单元从「页块」变成了「条」**，所以「v4 34.2%」与旧口径
   「v3 44.7%」**不可直接比**（后者金标指向页块，命中一块即算命中，天然更容易）。
   能与旧数并列的只有「同一套新金标下 v3 31.6% → v4 34.2%」这一对。
2. 合成金标在 v4 上**必须用迁移版**（`gold_synth_v4_*`）：它原本绑定被替换掉的 v1 行，
   直接拿旧金标跑 v4 会得到 43.5% 的假跌幅（那不是检索退步，是金标指不到行了）。

## 4. 本轮**没有**修好的部分（缺口，如实列）

仍有 **约 108 部法**是页块行（含用户截图那一部）：

- **94 部没有本地干净源**，其中包括：民法典侵权责任编解释（一）、治安管理处罚法、
  公司法、海商法、证券法、民事诉讼法、民用航空法、生态环境法典、监察法实施条例……
- **8 部源件版本过时**被拒用（例：民事诉讼法.md 是 2021 修正版，库里是更新版本）。

对这批我另外跑了一条**算法重建**路线（de-interleave + 分栏 DP + 条级切，见
`data/flk/tmp/` 与 worktree `sr-wt-v4-articles`），产出 10,628 条、覆盖 240 部法，
**但没有并入默认语料**，理由是它过不了金标复核：

- 真实金标 38 题里只有 **27 题**的 evidence 在重建文本中逐字命中，**9 题不命中**；
- 合成金标 177 题只有 **99 题**能解析到新行（大量条号因缺口对不上）；
- 该路线的作者本人也报了**静默夹带邻栏片段**的风险（抽检 10 个检索里有 3 个把邻栏一段塞进了本条）。

**结论**：这 108 部法正确的修法是**取官方文本**（flk.npc.gov.cn 国家法律法规数据库 /
发布机关官网），不是继续猜排版。这是下一步。

## 5. 下一步（按优先级）

1. **取官方文本补这 108 部**（含截图那一部）：flk 覆盖法律/行政法规/司法解释；需要浏览器会话
   （站点有 WAF，纯脚本被拦），沿用 `scripts/flk_bridge.py` 那套「页面取字节直接写盘」的做法。
2. 补完后重跑 `rebuild_corpus_v4.py`（`--replacement` 按优先级叠加）→ v6，并重跑金标复核。
3. 金标扩容到 200 题时，**按条级重建**（现在语料是条级的了，这一条才第一次真正可行）。
4. `README` 数字口径暂不变（仍是 v3），待 108 部补齐、口径稳定后一次性切到新口径并全站同步。

## 6. 复现

```bash
# 1) 干净源 → 条级片段（+ 报告 + 前后对照）
python scripts/resource_from_clean_files.py \
    --docs D:/Claudeworkspace/rag-server/documents \
    --ldb  D:/Claudeworkspace/legal-wisdom-app/data/database/legal.db \
    --corpus data/corpus_v3.jsonl \
    --out data/flk/fragments/clean_source.jsonl \
    --map data/flk/tmp/clean_source_row_map.json \
    --report data/flk/tmp/clean_source_report.txt \
    --md docs/resource-clean-source.md

# 2) 换进语料 + 金标条级迁移（evidence 逐题校验）
python scripts/rebuild_corpus_v4.py --base data/corpus_v3.jsonl \
    --replacement data/flk/fragments/clean_source.jsonl \
    --out data/corpus_v4.jsonl \
    --gold gold/gold_real_38.jsonl --gold-out gold/gold_real_38_v4.jsonl \
    --gold-synth gold/gold_synth_seed20260918.jsonl \
    --gold-synth-out gold/gold_synth_v4_seed20260918.jsonl

# 3) v4 上重新出评测（用迁移后的金标）
python scripts/gen_eval_report.py --corpus data/corpus_v4.jsonl \
    --corpus-label "v4（条级重建）" \
    --gold-real gold/gold_real_38_v4.jsonl \
    --gold-synth gold/gold_synth_v4_seed20260918.jsonl \
    --out-doc <out>.md --out-metrics <out>.json
```

（干净源目录与 legal.db 都是**本地数据，不入仓库**；仓库只带代码与派生金标。）
