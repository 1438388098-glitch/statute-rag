# -*- coding: utf-8 -*-
"""statute-rag：条文级法条混合检索底座（零第三方依赖）。

公开入口：
- statute_rag.retrieval   检索三件套（Like / BM25 / Hybrid RRF）
- statute_rag.query_expansion  口语↔法言法语词典扩展
- statute_rag.importer    条文导入与质检
- statute_rag.gold        合成金标生成
- statute_rag.eval_harness  Recall@k / MRR 评测
"""

__version__ = "0.1.1"
