# C1 执行计划 — oa_search 工具 + channel 透传

> 由 init-c1 产出、主 agent 自审通过。executor 严格照此实施。基线 **1772 passed**。
> 灰度：`OA_SEARCH_ENABLED` 默认 false → 合并后零行为变更。

## 分支
`git checkout -b batch/c1-oa-search`（LinChat 仓库 `/home/dantsinghua/work/linchat`）

## 改动 1：新建 `backend/apps/graph/tools/oa_search.py`

```python
"""公众号知识库检索工具

供主 Agent 调用，全文检索 we-mp-rss 公众号文章库（Phase A 建的 FTS5 trigram 索引）。
只读独立 sidecar SQLite（oa_fts.db），不写、不经 ORM —— 外部只读副本，非 PostgreSQL 可信源。
检索逻辑内联复用 oa_indexer.search()：短词(<3字符)走 LIKE 兜底，长词走 FTS MATCH + snippet 高亮。
"""

import asyncio
import logging
import os
import re
import sqlite3
import time

from django.conf import settings
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool

logger = logging.getLogger(__name__)


def _fts_quote(query: str) -> str:
    # trigram 需 ≥3 字符；双引号包裹作短语，转义内部引号
    q = (query or "").strip().replace('"', '""')
    return f'"{q}"'


def _search_sync(db_path: str, query: str, limit: int) -> list[dict]:
    """阻塞 IO：只读打开 FTS 库并检索（复用 oa_indexer.search 逻辑）。"""
    if not db_path or not os.path.exists(db_path):
        logger.warning("oa_search: FTS db 不存在: %s", db_path)
        return []
    fts = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        q = (query or "").strip()
        if len(q) < 3:
            like = f"%{q}%"
            rows = fts.execute(
                "SELECT mp_name, title, url, publish_ts, substr(body,1,80) AS snip "
                "FROM oa WHERE title LIKE ? OR mp_name LIKE ? "
                "ORDER BY publish_ts DESC LIMIT ?",
                (like, like, int(limit))).fetchall()
        else:
            rows = fts.execute(
                "SELECT mp_name, title, url, publish_ts, "
                "  snippet(oa, 3, '<<', '>>', ' … ', 12) AS snip "
                "FROM oa WHERE oa MATCH ? ORDER BY rank LIMIT ?",
                (_fts_quote(q), int(limit))).fetchall()
    except sqlite3.OperationalError as e:
        logger.warning("oa_search: FTS 查询失败 q=%r err=%s", query, e)
        return []
    finally:
        fts.close()
    out = []
    for mp_name, title, url, pub, snip in rows:
        date = time.strftime("%Y-%m-%d", time.localtime(pub)) if pub else ""
        snip = re.sub(r"\s+", " ", snip or "").strip()
        out.append({"mp_name": mp_name or "", "title": title or "",
                    "url": url or "", "date": date, "snippet": snip})
    return out


@tool
async def oa_search(query: str, config: RunnableConfig, limit: int = 5) -> str:
    """检索公众号知识库（已订阅公众号的历史文章全文）。

    当用户问到某个话题/概念/事件，或想知道公众号里写过什么时使用。
    返回命中文章的公众号名、发布日期、高亮片段和原文链接。

    Args:
        query: 检索关键词或短语
        limit: 返回最多多少篇文章，默认 5 篇
    """
    from apps.graph.tools import cap_tool_result

    q = (query or "").strip()
    if not q:
        return "请提供检索关键词"

    db_path = getattr(settings, "OA_SEARCH_DB_PATH", "")
    limit = max(1, min(int(limit or 5), 20))
    try:
        results = await asyncio.to_thread(_search_sync, db_path, q, limit)
    except Exception as e:
        logger.exception("oa_search 执行异常: q=%r", q)
        return f"公众号知识库检索出错：{e}"

    if not results:
        return f"公众号知识库中未查到与「{q}」相关的文章。"

    lines = [f"在公众号知识库中找到 {len(results)} 篇与「{q}」相关的文章："]
    for i, r in enumerate(results, 1):
        head = f"{i}. [{r['mp_name']}｜{r['date']}] {r['title']}".rstrip()
        body = f"   {r['snippet']}" if r["snippet"] else ""
        url = f"   来源: {r['url']}" if r["url"] else ""
        lines.append("\n".join(x for x in (head, body, url) if x))

    return cap_tool_result("\n".join(lines), "oa_search")


OA_TOOLS = [oa_search]
```

## 改动 2：注册 `backend/apps/graph/subagents/__init__.py`
在 `tools.append(history_search)`（现 :54）之后插入：
```python
    # 公众号知识库检索工具：OA_SEARCH_ENABLED 开关灰度
    if getattr(settings, "OA_SEARCH_ENABLED", False):
        from apps.graph.tools.oa_search import oa_search

        tools.append(oa_search)
```
（`from django.conf import settings` 该文件顶部已导入。）**graph.py 不改**（get_subagent_tools 已被复用）。

## 改动 3：settings `backend/core/settings/third_party.py` 末尾追加
```python
# ============ 公众号知识库检索 (oa_search / wn-linchat-brain C1) ============
OA_SEARCH_DB_PATH = os.getenv(
    "OA_SEARCH_DB_PATH",
    "/home/dantsinghua/clawd/scripts/wechat-narrator/oa_fts.db",
)
OA_SEARCH_ENABLED = os.getenv("OA_SEARCH_ENABLED", "false").lower() == "true"
```

## 改动 4：channel 透传（4 处）

**4a. `backend/apps/graph/agent.py:138` get_agent_config**
```python
def get_agent_config(user_id: int, callbacks: Optional[list] = None, channel: str = "web") -> dict:
    config: dict = {"configurable": {
        "thread_id": get_thread_id(user_id), "user_id": str(user_id), "channel": channel,
    }}
    config["metadata"] = {"langfuse_tags": [f"channel:{channel}"], "channel": channel}
    if callbacks: config["callbacks"] = callbacks
    return config
```
（保持原有其他行不变；只加 channel 参数 + configurable.channel + metadata。`agent_service.py:226` resume 路径 `get_agent_config(user_id)` 不改，默认 web。）

**4b. `backend/apps/graph/services/agent_service.py:34`** execute 签名 `attachment_uuids` 后加 `channel: str = "web"`

**4c. `backend/apps/graph/services/agent_service.py:81`** → `get_agent_config(user_id, [langfuse_handler] if langfuse_handler else None, channel=channel)`

**4d. `backend/apps/chat/services/chat_service.py:43`** 调用 `AgentService.execute(...)` 末参加 `, channel="web"`

**4e. `backend/apps/voice/services/voice_pipeline.py:153`** 调用 `AgentService.execute(...)` 末参加 `, channel="voice"`（ambient 分支 :147-151 不改）

## 改动 5：测试 `backend/tests/apps/graph/test_oa_search.py`（新建）
参考 `test_subagents.py` 风格（`@pytest.mark.asyncio` + `tool.ainvoke` + patch settings）：
1. `test_oa_search_hit` — monkeypatch `_search_sync` 返回 2 条 → 含公众号名/日期/`来源:`/snippet/"找到 2 篇"
2. `test_oa_search_no_result` — 返回 `[]` → 含"未查到"，**不含 url**（防幻觉）
3. `test_oa_search_empty_query` — `query=""` → "请提供检索关键词"，不触 DB
4. `test_oa_search_short_query_like` — `query="AI"`(<3) → 走 LIKE 不抛错
5. `test_oa_search_cap` — 超长片段 → ≤ MAX_TOOL_RESULT_TOKENS 或含"[结果已截断]"
6. `test_oa_search_db_missing` — 路径不存在 → "未查到"不抛异常
7. `test_oa_search_registered` — patch `OA_SEARCH_ENABLED=True`(+BRAVE/HA 关) → `get_subagent_tools()` 含 oa_search；False → 不含

channel 透传 `backend/tests/apps/graph/test_agent_config.py`（新建/扩展）：
8. `test_get_agent_config_default_web` — `configurable.channel=="web"` + `metadata.langfuse_tags==["channel:web"]`
9. `test_get_agent_config_channel_voice` — channel="voice" 透传
10. `test_get_agent_config_channel_wechat` — channel="wechat"（为 C2 铺路）

## 验证（executor 必做，全绿才 commit）
1. 局部：`source linchat/bin/activate && cd backend && python -m pytest tests/apps/graph/test_oa_search.py tests/apps/graph/test_agent_config.py -q`
2. 全量门禁：`bash refactor/loop/validate_full.sh` → 必须 `rc=0 && failed=0`，**≥1772 passed**
3. 全绿后：`git add -A && git commit`（scope=graph）+ `git push -u origin batch/c1-oa-search`

## 红线合规
不裸改 PostgreSQL（oa_fts.db 是 wechat 侧外部只读副本）；无 migration；隔离粒度不涉 user（全局 KB）；对外 API 契约零变更；灰度默认关。
