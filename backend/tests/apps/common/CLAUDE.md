# tests/apps/common 测试指南

> **单用户单会话原则**: 一个用户永远对应一个会话，Message 中没有 conversation_id，只有 user_id。不存在多会话、不考虑并发会话。所有隔离按 user_id 粒度。

---

## 测试文件

| 文件 | 覆盖功能 | 测试函数数 |
|------|----------|-----------|
| `test_gateway_utils.py` | Gateway 请求头构建 / URL 获取 / 错误解析 / httpx 异常映射 / 重试机制 / Langfuse Span（单例重置 setup/teardown、不同步 flush 验证、文档解析 span） | 18 |

---

## 运行命令

```bash
cd /home/dantsinghua/work/linchat/backend && source ../linchat/bin/activate && pytest tests/apps/common/ -v
```

## 注意事项

1. 通过 mock httpx 和 settings 实现，无需真实 Gateway 服务
2. 异步测试使用 `pytest-asyncio`（`@pytest.mark.asyncio`）
3. `TestRecordGatewaySpan` 每个测试前后重置 `_langfuse_client` 单例（setup_method/teardown_method），验证 Langfuse 3.x `start_observation` API，不同步 flush（`flush.assert_not_called()`）
4. `TestRecordGatewaySpan` 覆盖成功 span、错误 span（level=ERROR）、文档解析 span（status_code=202）三种场景
