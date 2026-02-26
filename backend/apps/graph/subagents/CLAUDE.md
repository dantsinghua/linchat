# graph/subagents 指南

> SubAgent 子代理模块，主 Agent 通过工具调用委派任务给专属 SubAgent。
> 每个 SubAgent 内部创建独立的 `create_react_agent`，自动注入公共工具。

---

## 架构

```
主 Agent (create_chat_agent)
  ├── search_subagent     → SearchAgent (web_search + mem_search)
  ├── memory_subagent     → MemoryAgent (mem_search/cache/update/delete + web_search)
  ├── code_subagent       → CodeAgent (python_exec + mem_search + web_search)
  ├── ha_subagent         → HAAgent (ha_query/control/diagnose + mem_search + web_search)
  ├── multimodal_subagent → MultimodalAgent (multimodal_analyze + document_parse)
  └── history_search      → 直接工具（非 SubAgent），历史消息关键词搜索
```

---

## 文件结构

| 文件 | 职责 | 启用条件 |
|------|------|---------|
| `__init__.py` | `get_subagent_tools()` — 按条件组装可用 SubAgent 工具列表 | - |
| `base.py` | `run_subagent()` 工厂函数 + `get_common_tools()` 公共工具 | - |
| `search_agent.py` | `search_subagent` — 互联网搜索 | `BRAVE_SEARCH_API_KEY` 非空 |
| `memory_agent.py` | `memory_subagent` — 记忆 CRUD | 始终启用 |
| `code_agent.py` | `code_subagent` — Python 代码执行 | 始终启用 |
| `ha_agent.py` | `ha_subagent` — Home Assistant 智能家居 | `HA_ENABLED=True` |
| `multimodal_agent.py` | `multimodal_subagent` — 多媒体文件分析 | 始终启用（模型配置由内部检查） |

---

## __init__.py — get_subagent_tools()

按配置条件组装返回的工具列表：

1. `search_subagent` — 需要 `BRAVE_SEARCH_API_KEY`
2. `memory_subagent` — 始终启用
3. `code_subagent` — 始终启用
4. `ha_subagent` — 需要 `HA_ENABLED=True`
5. `multimodal_subagent` — 始终启用
6. `history_search` — 始终启用（直接工具，非 SubAgent）

---

## base.py 核心

### `run_subagent(task, config, tools, prompt, llm=None, name="subagent", timeout=None)`

SubAgent 工厂函数：
1. 从 `config` 提取 `user_id`
2. 获取 LLM 实例（优先传入 > Django `get_llm()` > 环境变量降级）
3. 合并专属工具 + 公共工具（`_merge_tools`，按工具名去重）
4. `create_react_agent(model, tools, prompt, name)` 创建内部 Agent
5. 转发父 config 的全部 configurable 键（支持 `attachment_uuids`/`stop_event` 等）
6. `asyncio.timeout(timeout)` 超时控制（默认 `SUBAGENT_TIMEOUT`=60s）
7. 统一异常处理：超时/限流/内容过滤/配额用尽

### `get_common_tools()`

所有 SubAgent 共享的公共工具：
- `mem_search` — 只读记忆查询（始终可用）
- `web_search` — 网络搜索（需要 `BRAVE_SEARCH_API_KEY`）

### `_merge_tools(specific_tools, common_tools)`

专属工具优先保留，公共工具中同名的跳过。

### `_get_llm_instance(llm=None)`

LLM 获取优先级：传入实例 > Django `get_llm()` > 环境变量配置。

---

## 各 SubAgent 详情

### search_subagent (search_agent.py)

- **专属工具**: `SEARCH_TOOLS` (`web_search`)
- **公共工具**: `mem_search`
- **Prompt 策略**: 搜索前先查用户记忆了解背景；结果用 `[[N]]` 标注引用来源，末尾附引文列表
- **超时**: 默认 60s

### memory_subagent (memory_agent.py)

- **专属工具**: `MEMORY_TOOLS` (`mem_search`, `mem_cache`, `mem_update`, `mem_delete`)
- **公共工具**: `web_search`（去重后 `mem_search` 跳过）
- **Prompt 策略**: 保存前必须先搜索去重；更新/删除前必须先获取 memory_id；保存精炼事实而非对话原文
- **超时**: 默认 60s

### code_subagent (code_agent.py)

- **专属工具**: `REPL_TOOLS` (`python_exec`)
- **公共工具**: `mem_search` + `web_search`
- **Prompt 策略**: 执行前查记忆获取上下文；涉及实时数据用 web_search；失败时可搜索解决方案后修正重试
- **超时**: 默认 60s

### ha_subagent (ha_agent.py)

- **专属工具**: `HA_TOOLS` (`ha_query`, `ha_control`, `ha_diagnose`)
- **公共工具**: `mem_search` + `web_search`
- **Prompt 策略**: 设备名模糊时先查列表找 entity_id；敏感操作（unlock/garage/automation off）返回确认提示
- **安全规则**: L3（解锁/车库门）/L4（禁用自动化）操作需用户确认
- **超时**: 默认 60s

### multimodal_subagent (multimodal_agent.py)

- **专属工具**: `multimodal_analyze` + `document_parse`
- **无公共工具注入**（通过 `run_subagent` 自动注入 `mem_search`/`web_search`）
- **超时**: `MULTIMODAL_SUBAGENT_TIMEOUT`（默认 1200s）
- **Prompt 策略**: 按附件类型选择工具 — 图片/视频/音频用 `multimodal_analyze`，PDF/DOCX 用 `document_parse`

#### multimodal_analyze 工具

1. 从 `config.configurable` 获取 `attachment_uuids`、`stop_event`、`request_id`
2. 加载附件，过滤掉 document 类型
3. 调用 `build_multimodal_messages()` 构建多模态消息
4. 从 DB 获取多模态模型配置（`model_service.get_active_model("multimodal")`）
5. 获取 GPU 锁（`acquire_gpu_lock`，互斥避免显存冲突）
6. 调用 `stream_multimodal_httpx()` 直连 MiniCPM-o 推理

#### document_parse 工具

1. 从 `config.configurable` 获取 `attachment_uuids`、`request_id`
2. 加载附件，过滤出 document 类型
3. 获取 GPU 锁
4. 创建解析任务（`DocumentParseService.parse_document`）
5. 同步轮询任务状态（间隔 `DOC_PARSE_POLL_INTERVAL`=3s，最长 `DOC_PARSE_POLL_MAX_WAIT`=900s）
6. 轮询期间续期推理任务 TTL（避免 Redis 键过期）
7. 获取 Markdown 格式结果（截断到 `DOC_PARSE_MAX_RESULT_LENGTH`=8000 字符）

---

## 关键导入路径

```python
from apps.graph.subagents import get_subagent_tools
from apps.graph.subagents.base import run_subagent, get_common_tools
from apps.graph.subagents.multimodal_agent import multimodal_subagent, multimodal_analyze, document_parse
```

## 测试 patch 路径

```python
@patch("apps.graph.subagents.base.run_subagent")
@patch("apps.graph.subagents.base.get_common_tools")
@patch("apps.graph.subagents.base._get_llm_instance")
@patch("apps.graph.subagents.multimodal_agent.multimodal_analyze")
@patch("apps.graph.subagents.multimodal_agent.document_parse")
```

---

## 注意事项

1. SubAgent 内部 LLM 输出通过 `parent_ids` 深度过滤，不推送到 SSE
2. `run_subagent` 转发父 config 的全部 `configurable` 键，确保 `attachment_uuids`、`stop_event` 等传递到工具
3. 多模态 SubAgent 超时远大于其他 SubAgent（1200s vs 60s），因为 GPU 推理和文档解析耗时长
4. GPU 锁（`acquire_gpu_lock`）确保同一时间只有一个 GPU 推理任务运行
