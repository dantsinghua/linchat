# tests/apps/graph 测试指南

> **单用户单会话原则**: 一个用户永远对应一个会话，Message 中没有 conversation_id，只有 user_id。不存在多会话、不考虑并发会话。所有隔离按 user_id 粒度。

---

## 测试文件

| 文件 | 覆盖功能 |
|------|----------|
| `test_subagents.py` | SubAgent 工厂 run_subagent（正常/超时/限流/内容过滤/配额/通用错误）/ 条件注册 / 事件过滤 |
| `test_subagent_autonomy.py` | SubAgent 自主性: code/search/memory 各自包含 common tools / 完整结果返回 / 单次调用 |
| `test_ha_subagent.py` | HA SubAgent 条件注册（HA_ENABLED 控制）/ 完整控制链路 / 不可达降级 / 认证错误友好提示 |
| `test_ha_client.py` | HAClient REST API: get_state/get_states/call_service/get_history/get_error_log/check_health/HTTP 错误 |
| `test_ha_tools.py` | ha_control/ha_query/ha_diagnose 三工具 / Redis 限流 / 黑名单 / 敏感操作分级(L3/L4) / ACTION_MAP 18 项 |

---

## 运行命令

```bash
cd /home/dantsinghua/work/linchat/backend && source ../linchat/bin/activate && pytest tests/apps/graph/ -v
```

## 核心 Mock

| Mock 目标 | 用途 |
|-----------|------|
| `apps.graph.subagents.base._get_llm_instance` | SubAgent 内部 LLM 实例 |
| `apps.graph.subagents.base.create_react_agent` | LangGraph Agent 创建 |
| `apps.graph.subagents.settings` | 环境变量（HA_ENABLED / BRAVE_SEARCH_API_KEY） |
| `apps.graph.tools.ha_client.httpx.AsyncClient` | HA REST API HTTP 请求 |
| `apps.graph.tools.ha_tools.ha_client` | HAClient 实例（工具层 mock） |
| `aioredis.from_url` | Redis 限流键（INCR/EXPIRE/TTL） |

## 注意事项

1. 无需真实 HA 实例或 Redis，全部通过 mock 实现
2. `HA_ENABLED` 通过 mock settings 控制，不依赖环境变量
3. 异步测试使用 `tests.helpers.run_async()` 辅助函数
4. 敏感操作分级: L3（unlock/garage）需确认，L4（automation）直接拒绝
