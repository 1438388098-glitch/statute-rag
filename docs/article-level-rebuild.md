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

## 3. v4 语料（过渡版；app 默认已切到 v5，见第 4 节）

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

## 4. 修法（二）：剩下 104 部，按证据换源、逐部留痕

第 3 节的 v4 只修了「本地干净源件对得上」的 132 部，另 104 部的 v1 页块行当时被整批
丢弃（宁缺勿滥）。本节把这 104 部补上：**103 部已换成语义正确的条级正文，1 部仍缺源**。

### 4.1 先说一条被自己量掉的路线（去交错）

最直觉的修法是「把交错文本按排版猜回去」。本仓确实写了这条路
（`scripts/deinterleave_gazette.py`：按公报页眉页脚切页 → 页内非空行隔行分股 → 用
「条号必须单调」裁决两股先后），但它**没有一条语料行来自它**，因为先在金样上量了一
遍：

| 路线 | 与 130 部干净源件金样逐条比对 |
|---|---|
| 去交错（`ldb-deinter`） | 逐字命中 1207 / 7340 = **16.4%** |
| **legal.db 原文按行重切（`ldb-raw`）** | 逐字命中 **7339 / 7340 = 100.0%**（129/130 部整法全中） |

关键事实：`legal.db` 的 `documents.content` **保留了行结构**（每行一个 PDF 文本行、
行间空行），语料里的「页块」是**入库时按长度切块**造成的，不是文本本身坏。所以对文本
没交错的法，`ldb-raw` 直接把行当段落重切即可，100% 复现金样。

去交错只对**真交错**的那批才有意义，而它在那一批上也不够可靠：拿用户报障那一部
（民法典侵权责任编解释（一））对比同版本镜像文本，去交错结果 25 条、镜像 26 条，且去交错
正文里**夹带了公报页眉/公告碎片**、还出现游离数字（"请求监护11人承担…"）——被门禁
（条号 1..N、句末标点）漏过，因为碎片是插在条文中间的。**猜排版会静默产假条文**，
所以这条路只作为「本地源兜底」留在脚本里，当前未命中任何法。

### 4.2 四源按优先级补，逐部可追

按证据强度排序，前一个源覆盖的法不再被后面的源替换
（`rebuild_corpus_v4.py --replacement` 按顺序优先）：

| 优先级 | 源 | 覆盖 | 性质与校验 |
|---|---|---|---|
| ① | **中国政府网政策文件库官方页面** | **3 部 / 187 条** | 官方源。政策文件库按法名检索到同名文书页面 → 抓正文 → 切条 → 与镜像逐条比对（NFKC 归一）后**用官方文本替换镜像**；带版本门（页面版本早于库内公布日期的一律不采用，避免把老公报扫描版当现行文本） |
| ② | 干净源件（官方 docx/md，即 v4 那批） | 132 部 / 7,340 条 | 官方源已在 v4 落地，本轮不动 |
| ③ | **公开法规文本镜像**（`LawRefBook/Laws`，GitHub） | **99 部 / 6,514 条** | 按官方公布体例整理的 markdown。**只取「文件名日期 == legal.db 公布日期」的版本**（`--date-strict`）；条号必须 1..N 全序、覆盖率 ≥ 0.35（带附表/附则的文书覆盖率天然低）、逐条门禁丢弃 ≤ 5% |
| ④ | legal.db 按行重切 / 整篇单元 | 3 部 / 37 条 | 见 4.1 的 100% 金样；小体量批复没有条号结构时整篇作一个引用单位（`unit=whole-document`） |
| ⑤ | 镜像（不限版本，仅作最后兜底） | 1 部 / 74 条 | `消防法`：镜像最新版是 2021-04-29 修正、库内登记 2019-04-23 → **版本上浮**，行内 `meta.version_bumped=true`，报告里逐部列出 |

镜像这批的**第二条校验**是跟另一个独立来源逐条对：26 部法在竹马（法考法条汇编）里也有
全文，两边在 NFKC 归一后逐条一致（公司法 266/266、海商法 310/310、治安管理处罚法
144/144、民事诉讼法 306/306…，24/26 部 100% 一致）。

### 4.3 官方页面核对：既核对，也替换

`scripts/verify_gov_official.py` 对 99 部镜像法做了官方核对：

| 结果 | 部数 | 处理 |
|---|---|---|
| 与官方现行版逐条一致 | 3 | 用官方文本替换镜像（药品管理法实施条例 89 条、婚姻登记条例 28 条、烈士褒扬条例 70 条） |
| 政策文件库里的页面是**旧版**（2000–2007 年老公报） | 17 | 不采用（版本门），保留镜像版本 |
| 政策文件库里没有该文书 | 79 | 法律/司法解释不在该库（在 flk 国家法律法规数据库与最高法官网，前者脚本被 WAF 挡），**如实记为「单一来源、未经官方逐字比对」** |

**为什么非要核对**：镜像的文件名日期不等于正文版本。实测反例：`婚姻登记条例(2025-04-06).md`
文件名是 2025 版，正文第一条却引「婚姻法」（2003 年版措辞），而国务院令第 804 号
（2025-04-06 第二次修订）第一条引的是「民法典」。只按文件名日期取，就会把旧版正文当新版
发出去——这正是本仓「按文件名日期严格匹配」还不够、必须再核一道的原因。

### 4.4 版本影响与金标后果（如实说）

换源后正文与**库内登记的版本**一致了，但库内正文有时落后于自己登记的日期。实测：
《治安管理处罚法》库内公布日期是 2025-06-27、正文却是 2012 版（罚款额度都对不上），
换源后为 2025 现行版——镜像与竹马两个独立来源给的都是 144 条现行版。后果是真实金标
里有 **2 题（拆开成 4 条 gold 行）的 evidence 不再逐字命中**：题干是按旧版文本写的
（「卖淫、嫖娼的，处十日以上十日以下拘留…」是 2012 版，现行版是「十五日以下…五千元
以下罚款」）。这不是语料错，是**金标过时**，要按现行文本重写。

合成金标同理：177 题里 9 题解析不到行、29 题的短语随换源/升版离开了目标行。只比
「短语仍在目标行内」的题：v4 96.6%（168/174）→ v5 **100.0%（139/139）**——所以合成
R@5 从 94.9% 掉到 86.4% **不是检索退步**。

### 4.5 残留与缺口

| 项 | 数 | 说明 |
|---|---|---|
| 仍缺源的法 | **1 部** | 最高人民法院关于基本医疗保险基金先行支付申请条件法律适用问题的批复：公报版面把「公告」块与双栏正文交错，本地按行重切/去交错、镜像都取不出干净整篇；**宁缺勿滥**，该部在语料里缺席并记入缺口清单 |
| 仍含多个条首的行（粗判据） | 43 行 / 0.2%（v3 为 61%） | 逐行看过：42 行是《刑法》「第一百二十条之一」这类同号之 N 条文（v2 官方段既有形态），1 行是正文引用邻条条号的单条——都不是页块 |
| 未经第二个独立来源核对的法 | 79 部 | 镜像单一来源 + 内部门禁（条号 1..N、逐条门禁、版本日期与库内一致）。要再上一道官方比对，需 flk 国家法律法规数据库（脚本被 WAF 挡，需浏览器会话）或最高法官网 |

### 4.6 语料 v5（已切换为 app 默认）

```
python scripts/rebuild_corpus_v4.py --base data/corpus_v3.jsonl     --replacement data/flk/fragments/gov_official.jsonl     --replacement data/flk/fragments/clean_source.jsonl     --replacement data/flk/fragments/mirror_strict.jsonl     --replacement data/flk/fragments/local_remaining.jsonl     --replacement data/flk/fragments/mirror_rest.jsonl     --out data/corpus_v5.jsonl --unrepaired drop-pageblock     --gold gold/gold_real_38.jsonl --gold-out gold/gold_real_38_v5.jsonl     --gold-synth gold/gold_synth_seed20260918.jsonl     --gold-synth-out gold/gold_synth_v5_seed20260918.jsonl
```

| 项 | v3 | v4 | **v5** |
|---|---|---|---|
| 行数 | 25,273 | 25,551 | **25,033** |
| 法律部数 | 432 | 432 | **431** |
| 删掉的 v1 页块行 | — | 14,212 | 14,205 |
| 新增条级行 | — | 7,340 | **13,965** |
| 真实金标 evidence 逐字复核 | — | 38/38 | **34/38**（4 题因《治安管理处罚法》升版而金标过时，见 4.4） |
| 合成金标解析到行 | — | 176/177 | 168/177 |

`--unrepaired drop-pageblock` 是本轮新加的判据：只丢「页块为主」的法（页块比例 ≥ 0.5），
v1 里本来已经是条级的法（小体量批复，比例多为 0）保留——v4 的 `drop` 是一刀切，误伤了
4 部法（它们在 v4 里整部缺席，v5 已恢复）。

**同一份代码、各自迁移金标下 v4 → v5**（真实问句 hybrid）：R@5 **34.2% → 47.4%**、
MRR@5 0.242 → 0.271、R@10 52.6% → **65.8%**、R@20 73.7% → **86.8%**、
R@30 81.6% → **94.7%**（38 题里只剩 2 题未进 top-30）。合成金标按上面的公平口径
96.6% → 100.0%。

## 5. 复现

```bash
# 1) 干净源件（官方 docx/md）→ 条级片段（v4 那批）
python scripts/resource_from_clean_files.py     --docs D:/Claudeworkspace/rag-server/documents     --ldb  D:/Claudeworkspace/legal-wisdom-app/data/database/legal.db     --corpus data/corpus_v3.jsonl     --out data/flk/fragments/clean_source.jsonl     --map data/flk/tmp/clean_source_row_map.json     --report data/flk/tmp/clean_source_report.txt     --md docs/resource-clean-source.md

# 2) 镜像源（版本日期严格匹配）→ 条级片段
git clone --depth 1 https://github.com/LawRefBook/Laws.git D:/Claudeworkspace/_lawrefbook
python scripts/lawrefbook_source.py --repo D:/Claudeworkspace/_lawrefbook     --ldb <legal.db> --corpus data/corpus_v3.jsonl     --skip data/flk/fragments/clean_source.jsonl --date-strict     --out data/flk/fragments/mirror_strict.jsonl --report data/flk/tmp/mirror_strict_report.txt

# 3) 本地源兜底（竹马 / legal.db 按行重切 / 整篇单元）→ 条级片段
python scripts/resource_local_remaining.py --ldb <legal.db> --corpus data/corpus_v3.jsonl     --covered data/flk/fragments/clean_source.jsonl     --covered data/flk/fragments/mirror_strict.jsonl     --out data/flk/fragments/local_remaining.jsonl --report data/flk/tmp/local_remaining_report.txt

# 4) 镜像兜底（不限版本，版本上浮逐部留痕）
python scripts/lawrefbook_source.py --repo <clone> --ldb <legal.db> --corpus data/corpus_v3.jsonl     --skip data/flk/fragments/clean_source.jsonl     --skip data/flk/fragments/mirror_strict.jsonl     --skip data/flk/fragments/local_remaining.jsonl     --out data/flk/fragments/mirror_rest.jsonl --report data/flk/tmp/mirror_rest_report.txt

# 5) 官方页面核对（中国政府网政策文件库）——既核对也替换
python scripts/verify_gov_official.py --frags data/flk/fragments/mirror_strict.jsonl     --ldb <legal.db> --cache data/flk/tmp/gov_cache     --out-report data/flk/tmp/gov_verify.txt     --emit-overrides data/flk/fragments/gov_official.jsonl

# 6) 组装 v5（步骤见 4.6）→ 出评测
python scripts/gen_eval_report.py --corpus data/corpus_v5.jsonl     --gold-real gold/gold_real_38_v5.jsonl     --gold-synth gold/gold_synth_v5_seed20260918.jsonl     --out-doc docs/eval_report.md --out-metrics docs/metrics.json
```

（干净源目录、legal.db、镜像克隆都是**本地数据，不入仓库**；仓库只带代码、派生金标与
评测产物。）
