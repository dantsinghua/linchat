# tests/apps/graph 测试指南

> graph 模块（Agent + SubAgent + 工具）的测试集。

---

## 测试文件

| 文件 | 测试目标 | 说明 |
|------|---------|------|
| `test_subagents.py` | SubAgent 架构 | `get_subagent_tools()` 条件组装、`run_subagent()` 工厂函数 |
| `test_subagent_autonomy.py` | SubAgent 自主性 | SubAgent 独立完成任务、不回传不完整结果 |
| `test_ha_subagent.py` | HA SubAgent | `ha_subagent` 工具调用委派 |
| `test_ha_client.py` | HAClient | HA REST API 调用、异常处理 |
| `test_ha_tools.py` | HA 工具集 | `ha_query` / `ha_control` / `ha_diagnose` + 限流 + 黑名单 + 敏感操作 |

---

## Mock 策略

```python
@patch("apps.graph.subagents.base.run_subagent")
@patch("apps.graph.subagents.base._get_llm_instance")
@patch("apps.graph.tools.ha_client.HAClient.get_states")
@patch("apps.graph.tools.ha_client.HAClient.call_service")
```

HA 工具测试需 mock `aioredis.from_url` 进行限流键模拟。
