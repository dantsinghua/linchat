# PDF 文档解析失败 + Langfuse 集成缺陷分析报告

> 日期：2026-02-27
> 报告人：Claude Code
> 状态：待修复

---

## 1. 问题描述

用户上传 PDF 论文后，AI 回复"由于系统资源限制，目前无法直接解析PDF文档"，给出手动替代建议而非实际解析文档内容。

**截图表现**：AI 输出"让我使用多媒体分析工具来详细解析。很抱歉，由于系统资源限制，目前无法直接解析PDF文档。不过我可以为您提供几个替代建议的处理方式：..."

---

## 2. 全链路实测结果

### 2.1 文件上传链路 ✅

| 检查项 | 结果 |
|--------|------|
| MinIO 文件存储 | 正常，157KB，bucket=linchat-media |
| DB 记录 | uuid=0a8c9171，media_type=document，is_expired=False |
| 文件可下载 | 正常，从 MinIO 可读取完整 PDF |

### 2.2 Gateway 文档解析接口 ✅

```bash
# 直接调用测试
POST http://127.0.0.1:8100/v1/documents/parse
→ 202 Accepted, task_id=4a12b0a1-306d-4566-a2f6-3c6f1a4df2c5
```

### 2.3 文档解析端到端测试 ✅（GPU 可用时）

通过 LinChat 后端 `DocumentParseService` 直接调用：

```
15:02:13 — 创建任务: task_id=4a12b0a1, model=minicpm-o, pages=3
15:02:19 — 轮询 #1: status=processing
15:02:25 — 轮询 #2: status=processing
15:02:31 — 轮询 #3: status=completed ✅
15:02:32 — 获取结果: 1027 字符 Markdown
```

**解析内容正确**：输出了 PDF 中的"中英金融工作组首次会议共识文件"完整内容。

### 2.4 文档解析失败场景 ❌（GPU 不可用时）

在 GPU 显存不足时（23.84/23.89GB, 99.8%）：

```
14:48:10 — 创建任务: task_id=2675fb56, model=minicpm-o
14:48:22~14:49:28 — 轮询 #1-#3: status=pending（模型无法加载）
14:49:39 — 轮询 #4: status=failed
           error_message: "GPU 显存持续不足，请稍后重试或释放其他模型"
```

### 2.5 Gateway 健康状态（故障时）

```json
{
  "model_service": "unhealthy — llama.cpp service health check failed",
  "gpu": { "used_gb": 23.84, "total_gb": 23.89, "free_gb": 0.06, "utilization_pct": 99.8 },
  "minicpm-o": { "status": "unloaded", "vram_usage_gb": 18.0 }
}
```

**所有模型均为 unloaded，但 23.84GB 显存被占用**——残留进程或外部服务未释放。

---

## 3. 根因分析

### 3.1 PDF 解析失败的直接原因

**GPU 显存不足 → minicpm-o 模型无法加载 → 解析任务失败**

完整链路：
```
GPU 被未知进程占满 (23.84GB/23.89GB)
  → minicpm-o (需 18GB) 无法加载
  → Gateway 解析任务 pending 89s → failed: "GPU 显存持续不足"
  → LinChat document_parse 工具轮询到 status="failed"
  → 返回错误消息: "[xxx.pdf] 解析失败: GPU 显存持续不足，请稍后重试或释放其他模型"
  → SubAgent LLM 将错误传递给主 Agent
  → 主 LLM 生成用户可见的 "由于系统资源限制" 回复
```

**结论**：这是**基础设施问题**，非代码 bug。当 GPU 可用时，文档解析功能完全正常。

### 3.2 Langfuse trace_id 格式不兼容（代码 Bug）

**位置**: `backend/apps/chat/services/chat_service.py:61`

```python
request_id = f"req_{uuid.uuid4().hex[:16]}"
# 生成: "req_c9767911e1cd44bb" — 含 req_ 前缀 + 仅 16 字符
```

**Langfuse 3.12.0 要求**: trace_id 必须是 **32 位小写十六进制字符串**

**影响链路**:
1. `agent_service.py:274` 将 request_id 传入 `LangfuseCallbackHandler(trace_context={"trace_id": request_id})`
2. 每次 `on_chain_start` 回调 → `_create_remote_parent_span()` → `int(trace_id, 16)` → ValueError
3. 每次请求产生 **14+ 次 ValueError 日志**，所有 trace 数据丢失

**关键验证**：通过阅读 Langfuse SDK 源码确认：
- `client.py:1802` 无条件执行 `int(trace_id, 16)`，格式不对直接抛 ValueError
- `CallbackHandler.py:370` 捕获异常（`langfuse_logger.exception(e)`），不传播到 LangChain
- LangChain `handle_event()` 的 `handler.raise_error` 默认 False → **不中断 Agent 执行**

**结论**：不影响功能，但**完全丧失 Langfuse 监控能力**。

### 3.3 Langfuse `trace()` API 已移除（代码 Bug）

**位置**: `backend/apps/graph/services/agent_service.py:280`

```python
handler.client.trace(id=request_id, metadata=multimodal_metadata, tags=...)
```

Langfuse 3.12.0 已将 `Langfuse.trace()` 方法移除，迁移为 OTel span API。调用抛出 `AttributeError: 'Langfuse' object has no attribute 'trace'`。

**影响**：每次 span 记录都会输出 DEBUG 级别错误日志，多模态 metadata 无法注入 trace。

### 3.4 document_parse 工具缺少诊断日志

当前工具在以下关键节点没有 INFO 级别日志：
- 创建解析任务后的 task_id
- 轮询最终状态（completed/failed/timeout）
- 获取结果的内容长度
- 工具函数的最终返回值

导致从后端日志无法直接判断"文档解析到底返回了什么给 LLM"。

---

## 4. 修复方案

### Fix 1：修复 request_id 格式（兼容 Langfuse 3.x）

**文件**: `backend/apps/chat/services/chat_service.py:61`

```python
# Before:
request_id = f"req_{uuid.uuid4().hex[:16]}"

# After:
request_id = uuid.uuid4().hex  # 32 位小写十六进制
```

**兼容性确认**：request_id 在以下场景均为字符串类型，格式变更无影响：
- `Message.request_id`（CharField）
- Redis 推理任务键值
- 前端 SSE `request_id`
- 活跃生成管理 `_active_generations` 键
- GPU 锁 owner 值

### Fix 2：修复 Langfuse metadata 注入方式

**文件**: `backend/apps/graph/services/agent_service.py` (`_init_langfuse` 函数)

```python
# Before (line 273-286):
handler = LangfuseCallbackHandler(
    trace_context={"trace_id": request_id},
)
if multimodal_metadata and handler.client:
    try:
        handler.client.trace(id=request_id, metadata=..., tags=...)  # ❌ API 已移除
    except Exception as e:
        logger.debug(...)

# After:
handler = LangfuseCallbackHandler(
    trace_context={"trace_id": request_id},
    metadata=multimodal_metadata if multimodal_metadata else None,
    tags=(
        ["multimodal"] + multimodal_metadata.get("media_types", [])
    ) if multimodal_metadata else None,
)
# 删除 handler.client.trace() 整个 if 块
```

### Fix 3：增加 document_parse 关键日志

**文件**: `backend/apps/graph/subagents/multimodal_agent.py`

添加 5 处 INFO 级别日志：

1. 创建任务后: `logger.info("文档解析任务创建: task_id=%s, file=%s", task_id, doc.file_name)`
2. 轮询到 completed: `logger.info("文档解析完成: task_id=%s, elapsed=%ds", task_id, elapsed)`
3. 轮询到 failed: `logger.info("文档解析失败: task_id=%s, error=%s", task_id, error_msg)`
4. 获取结果后: `logger.info("文档解析结果: file=%s, length=%d", doc.file_name, len(content))`
5. 函数返回前: `logger.info("文档解析工具返回: files=%d, total_length=%d", len(results), total_len)`

### Fix 4：锁定 Langfuse 版本

**文件**: `backend/requirements.txt`

```
# Before:
langfuse>=2.0.0

# After:
langfuse>=3.12.0,<4.0.0
```

---

## 5. 修改文件清单

| 文件 | 修改内容 | 影响面 |
|------|---------|--------|
| `backend/apps/chat/services/chat_service.py` | line 61: request_id 格式 | 低：仅字符串格式变更 |
| `backend/apps/graph/services/agent_service.py` | `_init_langfuse()` 重构 | 低：仅 Langfuse 初始化方式 |
| `backend/apps/graph/subagents/multimodal_agent.py` | 增加 5 处 INFO 日志 | 无：仅新增日志 |
| `backend/requirements.txt` | langfuse 版本约束 | 低：锁定已安装版本 |

---

## 6. 验证步骤

### 6.1 运行测试
```bash
pytest tests/chat/ tests/voice/ -v
```

### 6.2 Langfuse 修复验证
- 重启后端后发送普通消息
- 检查日志无 `ValueError: invalid literal for int() with base 16`
- 检查 Langfuse 面板（http://www.greydan.xin:8081）有新 trace

### 6.3 文档解析日志验证
- 上传 PDF 并发消息
- 检查后端日志含 `文档解析任务创建` / `文档解析完成` 等新增日志

### 6.4 GPU 基础设施（单独处理）
- Gateway 机器: `nvidia-smi` 检查 GPU 占用
- 确认 `GET /health` model_service: healthy
- 确认 minicpm-o 可正常加载
