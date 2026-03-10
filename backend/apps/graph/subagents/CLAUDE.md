# graph/subagents 指南

> **单用户单会话原则**: 一个用户永远对应一个会话，Message 中没有 conversation_id，只有 user_id。不存在多会话、不考虑并发会话。所有隔离按 user_id 粒度。

SubAgent 子代理模块，主 Agent 通过工具调用委派任务给专属 SubAgent。graph 是 LangGraph Agent Pipeline 的核心。

---

## 架构

```
主 Agent (create_chat_agent)
  ├── search_subagent      → web_search + mem_search
  ├── memory_subagent      → mem_search/cache/update/delete + web_search
  ├── code_subagent        → python_exec + mem_search + web_search
  ├── ha_subagent          → ha_query/control/diagnose + mem_search + web_search
  ├── multimodal_subagent  → multimodal_analyze + mem_search + web_search
  ├── document_subagent    → document_parse + doc_rag_search + mem_search + web_search (011 新增)
  └── history_search       → 直接工具（非 SubAgent）
```

---

## 文件结构

| 文件 | 职责 | 启用条件 |
|------|------|---------|
| `__init__.py` | `get_subagent_tools()` -- 按条件组装可用 SubAgent 工具列表 + `history_search` | - |
| `base.py` | `run_subagent()` 工厂函数 + `get_common_tools()` + `_merge_tools()` + `_get_llm_instance()` | - |
| `search_agent.py` | `search_subagent` -- 互联网搜索 | `BRAVE_SEARCH_API_KEY` 非空 |
| `memory_agent.py` | `memory_subagent` -- 记忆 CRUD | 始终启用 |
| `code_agent.py` | `code_subagent` -- Python 代码执行 | 始终启用 |
| `ha_agent.py` | `ha_subagent` -- Home Assistant 智能家居 | `HA_ENABLED=True` |
| `multimodal_agent.py` | `multimodal_subagent` + `multimodal_analyze` -- 图片/视频/音频分析 | 始终启用 |
| `document_agent.py` | `document_subagent` + `document_parse` + `doc_rag_search` -- 文档解析+RAG 检索（011 新增） | 始终启用 |

---

## base.py 核心

### `run_subagent(task, config, tools, prompt, llm=None, name, timeout=None)`

1. 从 config 提取 `user_id`（`_get_user_id`）
2. 获取 LLM（`_get_llm_instance`：传入 > Django `get_llm()` > 环境变量降级）
3. 合并专属工具 + 公共工具（`_merge_tools`，按名去重，专属优先）
4. `create_react_agent` 创建内部 Agent（设置 `prompt` 和 `name`）
5. 转发父 config 全部 configurable 键（`attachment_uuids`/`stop_event`/`request_id` 等）
6. `asyncio.timeout` 超时控制（默认 `SUBAGENT_TIMEOUT` = 60s）
7. 异常处理：超时/限流(`LLMRateLimitError`)/内容过滤(`LLMContentFilterError`)/配额用尽(`LLMQuotaExceededError`)

### `get_common_tools()`

所有 SubAgent 共享：`mem_search`（始终）+ `web_search`（需 BRAVE_SEARCH_API_KEY）。

---

## 各 SubAgent 详情

### search_subagent -- 互联网搜索

专属工具：`web_search`（SEARCH_TOOLS）。策略：先查记忆了解背景，结果用 `[[N]]` 引用并附参考来源列表。超时 60s。

### memory_subagent -- 记忆管理

专属工具：`mem_search/cache/update/delete`（MEMORY_TOOLS）。策略：保存前先搜索去重，保存精炼事实；更新 vs 删除按用户意图区分。超时 60s。

### code_subagent -- 代码执行

专属工具：`python_exec`（REPL_TOOLS）。策略：执行前查记忆和实时数据，失败可搜索方案后重试。超时 60s。

### ha_subagent -- 智能家居

专属工具：`ha_query/control/diagnose`（HA_TOOLS）。策略：设备名模糊先查列表；敏感操作（L3 解锁/车库、L4 禁用自动化）返回确认提示。超时 60s。

### multimodal_subagent -- 多媒体分析

专属工具：`multimodal_analyze`（定义在 multimodal_agent.py 内）。超时 1200s（`MULTIMODAL_SUBAGENT_TIMEOUT`）。
入口函数在 task 中注入附件数量提示（`[系统：用户已上传 N 个附件...]`），确保内部 LLM 知道附件存在并调用工具。

MULTIMODAL_PROMPT 核心规则：收到分析请求必须直接调用工具，不质疑附件是否存在；multimodal_analyze 处理图/视/音，文档类型由 document_subagent 处理。

#### multimodal_analyze

从 `config.configurable` 获取 `attachment_uuids` + `stop_event` + `request_id` -> 加载附件（排除 document 类型）-> `build_multimodal_messages` -> 获取多模态模型配置 -> 获取 GPU 锁 -> `stream_multimodal_httpx` 直连推理。

### document_subagent -- 文档解析+RAG（011 新增）

**文件**: `document_agent.py`（333 行，从 multimodal_agent.py 分离独立）

专属工具：`document_parse` + `doc_rag_search`。超时 1200s。

#### document_parse

从 `config.configurable` 获取 `attachment_uuids` + `request_id` -> 加载文档附件（仅 document 类型）-> 缓存检查（MediaAttachment.parsed_content）-> 获取 GPU 锁 -> `DocumentParseService.create_parse_task` -> 轮询状态（`DOC_PARSE_POLL_INTERVAL` 3s 间隔，`DOC_PARSE_POLL_MAX_WAIT` 最长 900s）-> SSE 进度推送（DOC_PARSE_PROGRESS）-> 获取 Markdown 结果（`DOC_PARSE_MAX_RESULT_LENGTH` 截断 8000 字符）-> 缓存到 MediaAttachment.parsed_content。轮询期间续期推理任务 TTL + 里程碑日志记录。

#### doc_rag_search

从 DocumentChunkEmbedding 表中通过 pgvector 向量搜索匹配文档分块，返回相关内容片段。用于已解析文档的精准检索。

---

## 关键导入路径

```python
from apps.graph.subagents import get_subagent_tools
from apps.graph.subagents.base import run_subagent, get_common_tools
from apps.graph.subagents.multimodal_agent import multimodal_subagent, multimodal_analyze, document_parse
```

---

## 注意事项

1. SubAgent 内部 LLM 输出通过 `parent_ids` 深度 > 3 过滤，不推送到 SSE
2. `run_subagent` 转发父 config 全部 configurable 键，确保附件/取消信号/request_id 传递
3. 多模态 SubAgent 超时远大于其他（1200s vs 60s），因 GPU 推理和文档解析耗时长
4. GPU 锁（`acquire_gpu_lock`）确保同一时间只有一个 GPU 推理任务运行
5. `multimodal_analyze` 和 `document_parse` 的 GPU 锁引用路径是 `apps.graph.services.gpu_lock`
6. `document_agent.py` 内 `document_parse` 含全链路日志（启动/轮询里程碑/失败/超时/结果获取）
