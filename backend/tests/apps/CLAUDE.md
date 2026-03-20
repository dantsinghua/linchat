# tests/apps 测试指南

> **单用户单会话原则**: 一个用户永远对应一个会话，Message 中没有 conversation_id，只有 user_id。不存在多会话、不考虑并发会话。所有隔离按 user_id 粒度。

---

## 目录结构

```
tests/apps/
├── common/
│   └── test_gateway_utils.py
├── graph/
│   ├── test_ha_client.py
│   ├── test_ha_subagent.py
│   ├── test_ha_tools.py
│   ├── test_subagent_autonomy.py
│   ├── test_subagents.py
│   └── test_document_agent.py
└── models/
    └── __init__.py (空)
```

---

## 测试文件

### common/

| 文件 | 覆盖功能 |
|------|----------|
| `test_gateway_utils.py` | Gateway 请求头构建 / URL 获取 / 错误解析 / httpx 异常映射 / 重试机制 / Langfuse Span |

### graph/

| 文件 | 覆盖功能 |
|------|----------|
| `test_subagents.py` | SubAgent 工厂 run_subagent / 条件注册 / 错误处理 / 事件过滤 / 边缘情况 |
| `test_subagent_autonomy.py` | SubAgent 自主性（独立工具集 / 完整结果返回 / 单次调用） |
| `test_ha_subagent.py` | HA SubAgent 条件注册 / 集成流程 / 降级处理 / Prompt 验证 / 工具导入 |
| `test_ha_client.py` | HAClient REST API（状态/服务/历史/健康检查/HTTP 错误处理） |
| `test_ha_tools.py` | HA 工具集（ha_control/ha_query/ha_diagnose）/ 限流 / 黑名单 / 敏感操作 L1-L4 |

---

## 运行命令

```bash
cd /home/dantsinghua/work/linchat/backend && source ../linchat/bin/activate && pytest tests/apps/ -v
# 按子模块
pytest tests/apps/common/ -v
pytest tests/apps/graph/ -v
```

## 注意事项

1. HA 测试通过 mock `httpx.AsyncClient` 和 `HAClient` 实现，无需真实 Home Assistant
2. Redis 限流测试通过 mock `aioredis` 实现，无需真实 Redis
3. `HA_ENABLED` 和 `BRAVE_SEARCH_API_KEY` 通过 mock settings 控制
4. 异步测试使用 `tests.helpers.run_async()` 辅助函数
5. HA 工具测试覆盖 L1-L4 四级安全模型，L4 操作直接拒绝
