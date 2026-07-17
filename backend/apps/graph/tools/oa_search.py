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
