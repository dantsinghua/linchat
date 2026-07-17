"""oa_search 公众号知识库检索工具单元测试 (wn-linchat-brain C1)

测试覆盖:
1. 命中：返回公众号名/日期/来源/snippet/"找到 N 篇"
2. 无命中：返回"未查到"，不含 url（防幻觉）
3. 空 query：不触 DB
4. 短词 (<3 字符)：走 LIKE 兜底不抛错
5. cap_tool_result 截断
6. DB 路径不存在：返回"未查到"不抛异常
7. get_subagent_tools 条件注册（OA_SEARCH_ENABLED 开关）
"""

from unittest.mock import patch

import pytest


@pytest.mark.asyncio
async def test_oa_search_hit():
    """命中 2 条 → 含公众号名/日期/来源:/snippet/"找到 2 篇"。"""
    from apps.graph.tools.oa_search import oa_search

    fake_rows = [
        {"mp_name": "机器之心", "title": "大模型综述", "url": "https://mp.weixin.qq.com/a",
         "date": "2026-01-01", "snippet": "关于 <<Transformer>> 的介绍"},
        {"mp_name": "量子位", "title": "Agent 前沿", "url": "https://mp.weixin.qq.com/b",
         "date": "2026-02-02", "snippet": "自主 <<Agent>> 应用"},
    ]
    with patch("apps.graph.tools.oa_search._search_sync", return_value=fake_rows):
        result = await oa_search.ainvoke(
            input={"query": "Transformer", "limit": 5},
            config={"configurable": {"user_id": 1}},
        )
    assert "找到 2 篇" in result
    assert "机器之心" in result
    assert "量子位" in result
    assert "2026-01-01" in result
    assert "来源:" in result
    assert "https://mp.weixin.qq.com/a" in result
    assert "Transformer" in result


@pytest.mark.asyncio
async def test_oa_search_no_result():
    """无命中 → 含"未查到"，不含任何 url（防幻觉）。"""
    from apps.graph.tools.oa_search import oa_search

    with patch("apps.graph.tools.oa_search._search_sync", return_value=[]):
        result = await oa_search.ainvoke(
            input={"query": "根本不存在的话题xyz", "limit": 5},
            config={"configurable": {"user_id": 1}},
        )
    assert "未查到" in result
    assert "来源:" not in result
    assert "http" not in result


@pytest.mark.asyncio
async def test_oa_search_empty_query():
    """空 query → "请提供检索关键词"，不触 DB。"""
    from apps.graph.tools.oa_search import oa_search

    with patch("apps.graph.tools.oa_search._search_sync") as mock_search:
        result = await oa_search.ainvoke(
            input={"query": "   ", "limit": 5},
            config={"configurable": {"user_id": 1}},
        )
    assert result == "请提供检索关键词"
    mock_search.assert_not_called()


@pytest.mark.asyncio
async def test_oa_search_short_query_like(tmp_path):
    """短词 (<3 字符) → 走 LIKE 分支不抛错（用真实内存/临时 sqlite 库验证）。"""
    import sqlite3

    from apps.graph.tools.oa_search import _search_sync

    db_path = str(tmp_path / "oa_fts.db")
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE oa(mp_name TEXT, title TEXT, url TEXT, publish_ts INTEGER, body TEXT)"
    )
    conn.execute(
        "INSERT INTO oa VALUES (?,?,?,?,?)",
        ("AI 前线", "AI 大爆发", "https://x", 1700000000, "正文内容 AI"),
    )
    conn.commit()
    conn.close()

    # "AI" 长度 2 < 3，应走 LIKE 分支
    rows = _search_sync(db_path, "AI", 5)
    assert isinstance(rows, list)
    assert any("AI" in r["title"] for r in rows)


@pytest.mark.asyncio
async def test_oa_search_cap():
    """超长片段 → ≤ MAX_TOOL_RESULT_TOKENS 或含"[结果已截断]"。"""
    from apps.graph.tools.oa_search import oa_search

    long_snip = "很长的内容片段" * 2000
    fake_rows = [
        {"mp_name": "测试号", "title": "长文", "url": "https://x",
         "date": "2026-01-01", "snippet": long_snip},
    ]
    with patch("apps.graph.tools.oa_search._search_sync", return_value=fake_rows):
        result = await oa_search.ainvoke(
            input={"query": "长文测试", "limit": 5},
            config={"configurable": {"user_id": 1}},
        )
    from apps.common.tokenizer import count_tokens
    from django.conf import settings
    max_tokens = getattr(settings, "MAX_TOOL_RESULT_TOKENS", 1500)
    assert "[结果已截断]" in result or count_tokens(result) <= max_tokens


@pytest.mark.asyncio
async def test_oa_search_db_missing():
    """DB 路径不存在 → 返回"未查到"，不抛异常。"""
    from apps.graph.tools.oa_search import oa_search

    with patch.object(
        __import__("django.conf", fromlist=["settings"]).settings,
        "OA_SEARCH_DB_PATH",
        "/nonexistent/path/oa_fts.db",
        create=True,
    ):
        result = await oa_search.ainvoke(
            input={"query": "任何查询", "limit": 5},
            config={"configurable": {"user_id": 1}},
        )
    assert "未查到" in result


def test_oa_search_registered():
    """OA_SEARCH_ENABLED=True 时 get_subagent_tools 含 oa_search；False 不含。"""
    with patch("apps.graph.subagents.settings") as mock_settings:
        mock_settings.BRAVE_SEARCH_API_KEY = ""
        mock_settings.HA_ENABLED = False
        mock_settings.OA_SEARCH_ENABLED = True
        from apps.graph.subagents import get_subagent_tools

        names = [t.name for t in get_subagent_tools()]
        assert "oa_search" in names

    with patch("apps.graph.subagents.settings") as mock_settings:
        mock_settings.BRAVE_SEARCH_API_KEY = ""
        mock_settings.HA_ENABLED = False
        mock_settings.OA_SEARCH_ENABLED = False
        from apps.graph.subagents import get_subagent_tools

        names = [t.name for t in get_subagent_tools()]
        assert "oa_search" not in names
