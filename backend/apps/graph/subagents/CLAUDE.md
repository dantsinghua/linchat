# graph/subagents 指南

> **单用户单会话原则**: 一个用户永远对应一个会话，Message 中没有 conversation_id，只有 user_id。所有隔离按 user_id 粒度。

SubAgent 子代理模块，主 Agent 通过工具调用委派任务给专属 SubAgent。

---

## 架构

```
主 Agent (create_chat_agent)
  ├── search_subagent      → web_search + mem_search
  ├── memory_subagent      → mem_search/cache/update/delete + web_search
  ├── code_subagent        → python_exec + mem_search + web_search
  ├── ha_subagent          → ha_query/control/diagnose + mem_search + web_search
  ├── multimodal_subagent  → multimodal_analyze + mem_search + web_search
  ├── document_subagent    → doc_list/doc_read/doc_search/document_parse + mem_search + web_search
  └── history_search       → 直接工具（非 SubAgent）
```

---

## 文件清单

| 文件 | 职责 | 行数 |
|------|------|------|
| `__init__.py` | `get_subagent_tools()` — 按配置条件组装可用 SubAgent 工具列表 | ~57 |
| `base.py` | `run_subagent()` 工厂 + `get_common_tools()` + `_merge_tools()` + `_get_llm_instance()` | ~100 |
| `search_agent.py` | `search_subagent` — 互联网搜索 | ~20 |
| `memory_agent.py` | `memory_subagent` — 记忆 CRUD | ~20 |
| `code_agent.py` | `code_subagent` — Python 代码执行 | ~20 |
| `ha_agent.py` | `ha_subagent` — Home Assistant 智能家居 | ~25 |
| `multimodal_agent.py` | `multimodal_subagent` + `multimodal_analyze` — 图片/视频/音频分析 | ~51 |
| `document_agent.py` | `document_subagent` + 4 个文档工具（doc_list/doc_read/doc_search/document_parse） | ~175 |
| `document_parse_helpers.py` | 文档解析辅助函数（从 document_agent.py 提取） | ~103 |

---

## SubAgent 列表

| SubAgent | 专属工具 | 超时 | 启用条件 | recursion_limit |
|----------|----------|------|----------|-----------------|
| `search_subagent` | `web_search` (SEARCH_TOOLS) | 60s | `BRAVE_SEARCH_API_KEY` 非空 | 默认 |
| `memory_subagent` | `mem_search/cache/update/delete` (MEMORY_TOOLS) | 60s | 始终启用 | 默认 |
| `code_subagent` | `python_exec` (REPL_TOOLS) | 60s | 始终启用 | 默认 |
| `ha_subagent` | `ha_query/control/diagnose` (HA_TOOLS) | 60s | `HA_ENABLED=True` | 默认 |
| `multimodal_subagent` | `multimodal_analyze` | 1200s | 始终启用 | 默认 |
| `document_subagent` | `doc_list/doc_read/doc_search/document_parse` | 1200s | 始终启用 | 40 |

所有 SubAgent 自动合并公共工具：`mem_search`（始终）+ `web_search`（需 BRAVE_SEARCH_API_KEY）。

---

## base.py 核心函数

| 函数 | 说明 |
|------|------|
| `run_subagent(task, config, tools, prompt, llm, name, timeout, recursion_limit)` | SubAgent 工厂：获取 LLM → 合并工具 → create_react_agent → 超时控制 → 异常处理 |
| `get_common_tools()` | 返回公共工具列表：`mem_search` + 可选 `web_search` |
| `_merge_tools(specific, common)` | 按名去重合并，专属工具优先 |
| `_get_llm_instance(llm)` | LLM 获取优先级：传入实例 > Django `get_llm()` > 环境变量降级 |

异常处理：`TimeoutError` / `GraphRecursionError` / `LLMRateLimitError` / `LLMContentFilterError` / `LLMQuotaExceededError`。

---

## document_parse_helpers.py 辅助函数

从 `document_agent.py` 提取的文档解析辅助逻辑：

| 函数 | 说明 |
|------|------|
| `extract_outline(content, max_headings)` | 从 Markdown 提取标题目录结构（`#{1,6}` 正则匹配） |
| `build_truncated_result(file_name, content, max_len, label)` | 构建截断结果：目录 + 内容预览 + 引导使用 doc_search |
| `poll_parse_task(task_id, doc, user_id, max_len)` | 轮询 Gateway 解析任务状态，发送 SSE 进度事件，处理 completed/failed/incomplete/timeout |

`poll_parse_task` 配置项：`DOC_PARSE_POLL_INTERVAL`（默认 3s）、`DOC_PARSE_POLL_MAX_WAIT`（默认 900s）。

---

## document_agent.py 工具详情

| 工具 | 说明 |
|------|------|
| `doc_list` | 列出用户文档附件，支持文件名搜索、时间范围筛选、排序（最多 20 条） |
| `doc_read` | 读取指定文档的解析结果全文（默认截断 8000 字符） |
| `doc_search` | 在已解析文档中检索内容，支持 keyword/semantic/hybrid 三种模式 |
| `document_parse` | 解析 PDF/DOCX：缓存检查 → GPU 锁 → Gateway 解析 → 轮询 → 保存缓存（支持 force 重新解析） |

---

## 依赖关系

```
base.py ← 所有 SubAgent（run_subagent, get_common_tools）
document_parse_helpers.py ← document_agent.py（build_truncated_result, poll_parse_task）
apps.context.loader.render ← 所有 SubAgent（Jinja2 prompt 模板）
apps.graph.tools/* ← search/memory/code/ha SubAgent（工具定义）
apps.graph.services.gpu_lock ← multimodal_agent, document_agent（GPU 互斥锁）
apps.media.services.document ← document_agent（DocumentParseService）
apps.media.services.document_cache ← document_agent（缓存读写）
apps.media.services.document_rag ← document_agent（RAG 向量检索）
apps.media.repositories ← document_agent（media_attachment_repo）
apps.common.event_service ← document_parse_helpers（SSE 进度事件）
```

---

## 关键导入路径

```python
from apps.graph.subagents import get_subagent_tools
from apps.graph.subagents.base import run_subagent, get_common_tools
from apps.graph.subagents.document_agent import document_subagent, document_parse
from apps.graph.subagents.multimodal_agent import multimodal_subagent, multimodal_analyze
```

---

## 注意事项

1. SubAgent 内部 LLM 输出通过 `parent_ids` 深度 > 3 过滤，不推送到 SSE
2. `run_subagent` 转发父 config 全部 configurable 键（attachment_uuids/stop_event/request_id 等）
3. 多模态和文档 SubAgent 超时 1200s，其余 60s（GPU 推理和文档解析耗时长）
4. GPU 锁（`acquire_gpu_lock`）确保同一时间只有一个 GPU 推理任务运行
5. `document_subagent` 设置 `recursion_limit=40`（其余使用默认值），因文档操作涉及多轮工具调用
6. `multimodal_analyze` 排除 document 类型附件，`document_parse` 仅处理 document 类型附件
