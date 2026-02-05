# Implementation Plan: 主对话流程 SubAgent 化重构

**Branch**: `006-subagent-tools` | **Date**: 2026-02-05 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/006-subagent-tools/spec.md`

## Summary

将 chat 主对话流程从"平铺工具绑定"模式重构为"SubAgent 委派"模式。主 agent 不再直接绑定搜索/记忆/代码执行等工具，而是将每类工具封装为独立的 SubAgent，主 agent 通过 LangChain tool 接口调用 SubAgent。每个 SubAgent 内部使用 `create_react_agent` 管理自己的工具链，对主 agent 而言是一个黑盒工具。

核心变更集中在 `backend/apps/graph/` 目录：重构 `agent.py` 中的 `create_chat_agent`、新增 SubAgent 定义模块、精简 `tool_usage.j2` prompt 模板。

## Technical Context

**Language/Version**: Python 3.11+ (后端)
**Primary Dependencies**: Django 4.2+, DRF 3.14+, LangGraph (create_react_agent), LangChain (ChatOpenAI, tool decorator), uvicorn 0.30+, redis-py (async), httpx
**Storage**: PostgreSQL 15 (主存储), Redis (缓存/PubSub)
**Testing**: pytest + pytest-django
**Target Platform**: Linux server (ASGI)
**Project Type**: Web application (后端重构，前端无变更)
**Performance Goals**: 普通对话响应退化 <10%，subagent 任务成功率 >=95%
**Constraints**: SubAgent 统一 60 秒超时，主 agent system prompt 工具描述 token 减少 50%+
**Scale/Scope**: 3 个 SubAgent (搜索/代码/记忆)，可扩展

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| 条款 | 要求 | 合规状态 |
|------|------|----------|
| 1.1 分层架构 | 视图层禁业务逻辑，服务层封装逻辑 | ✅ 不涉及视图层变更，agent 层属于服务层 |
| 1.2 SSE 视图规范 | ASGI 原生异步，禁止手动事件循环 | ✅ 不变更 SSE 视图，流式输出机制不变 |
| 1.3 数据一致性 | PostgreSQL 为主，写操作原子性 | ✅ 不涉及数据模型变更 |
| 2.1 Python 规范 | PEP 8 + Black + isort + 类型注解 | ✅ 将遵循 |
| 3.1 测试覆盖率 | 服务层 95%，总体 80%+ | ✅ 将编写对应测试 |
| 4.1 安全要求 | user_id 隔离，httpOnly Cookie | ✅ SubAgent 继承 user_id via RunnableConfig |
| 4.3 LLM 异常处理 | 统一异常类型处理 | ✅ SubAgent 内部错误由 subagent 自行处理后返回文本结果 |
| 4.4 术语定义 | user_id 粒度隔离 | ✅ 不引入新隔离粒度 |
| 8.2 ASGI 服务器 | 必须用 uvicorn | ✅ 不变更启动方式 |

**GATE 结果: PASS** — 无宪法违规。

## Project Structure

### Documentation (this feature)

```text
specs/006-subagent-tools/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output (本特性无数据模型变更，仅描述 SubAgent 接口)
├── quickstart.md        # Phase 1 output
└── tasks.md             # Phase 2 output (/speckit.tasks)
```

### Source Code (repository root)

```text
backend/
├── apps/
│   ├── graph/
│   │   ├── agent.py                 # 重构：create_chat_agent 改为使用 subagent tools
│   │   ├── graph.py                 # 更新：独立调试模式适配 subagent
│   │   ├── subagents/               # 新增：SubAgent 定义目录
│   │   │   ├── __init__.py          # SubAgent 注册表 + 条件加载
│   │   │   ├── base.py              # SubAgent 基类/工厂函数
│   │   │   ├── search_agent.py      # 搜索 SubAgent
│   │   │   ├── memory_agent.py      # 记忆 SubAgent
│   │   │   └── code_agent.py        # 代码执行 SubAgent
│   │   ├── tools/                   # 现有工具不变
│   │   │   ├── search.py            # 不变
│   │   │   ├── memory.py            # 不变
│   │   │   └── python_repl.py       # 不变
│   │   ├── services/
│   │   │   └── agent_service.py     # 微调：适配 subagent 监控事件
│   │   └── prompts.py               # 不变（兼容层）
│   └── context/
│       ├── builder.py               # 不变更（通过 tool_usage.j2 渲染，T006 精简模板即可）
│       └── templates/
│           └── tool_usage.j2        # 精简：移除具体工具使用指南，改为 subagent 委派指南
└── tests/
    └── apps/graph/
        ├── test_subagents.py        # 新增：SubAgent 单元测试
        └── test_agent.py            # 更新：适配新架构
```

**Structure Decision**: 在现有 `backend/apps/graph/` 下新增 `subagents/` 子目录，遵循现有项目结构。不引入新的 Django app，SubAgent 是 graph 模块的内部实现细节。

## Complexity Tracking

无宪法违规，无需记录复杂性权衡。

---

## Phase 0: Research

### R-001: SubAgent 作为 LangChain Tool 的实现模式

**Decision**: 每个 SubAgent 封装为一个 `@tool` 装饰的异步函数。函数内部创建 `create_react_agent`，执行 `agent.ainvoke()` 获取结果，返回文本字符串给主 agent。

**Rationale**:
- 与现有工具模式（`@tool` 装饰器）完全一致
- 主 agent 通过 LangChain 的 function calling 机制自动选择调用哪个 SubAgent
- SubAgent 内部的工具调用对主 agent 完全透明
- `ainvoke` 而非 `astream` 因为 SubAgent 结果需要作为整体返回给主 agent

**Alternatives considered**:
- LangGraph `Command` 模式 — 过于复杂，需要自定义 StateGraph，不适合"工具替换"场景
- 直接嵌套 StateGraph — 增加图结构复杂度，调试困难
- 简单函数包装（不用 agent）— 失去了 SubAgent 内部多步推理和自纠错能力

### R-002: SubAgent 的 user_id 传递机制

**Decision**: SubAgent tool 函数接收 `config: RunnableConfig` 参数，提取 `user_id`，然后将其传递给内部 agent 的 config。与现有工具（search/memory/repl）的 `_get_user_id(config)` 模式一致。

**Rationale**: 现有所有工具已经通过 `RunnableConfig.configurable.user_id` 获取用户标识，SubAgent 内部的工具调用自然继承这个机制。

### R-003: SubAgent 流式输出与 astream_events 的兼容性

**Decision**: SubAgent 内部使用 `ainvoke` 同步执行（非流式），将最终结果文本作为 tool result 返回给主 agent。主 agent 收到结果后，LLM 将其整合到自己的流式回复中。

**技术说明**: `ainvoke` 是指 SubAgent **agent 级别**的同步调用——主 agent 会等待 SubAgent 返回完整结果后才继续。但由于主 agent 使用 `astream_events` 消费事件流，SubAgent 内部 LLM 的 `on_chat_model_stream` 事件**仍会冒泡**到主 agent 的事件流中（因为 `astream_events` 是在主 agent 层面注册的，它能观测到所有嵌套执行的事件）。这不矛盾——SubAgent 对主 agent 而言是一个 tool（完整返回 tool result），但其内部 LLM token 级事件仍可被 `astream_events` 观测到，因此需要事件过滤。

**Rationale**:
- `astream_events` 会自动捕获嵌套 agent 内部的事件（包括 `on_tool_end`、`on_chat_model_end`）
- SubAgent 内部的 LLM 调用会产生 `on_chat_model_stream` 事件，但通过事件过滤（检查 `run_id` 或 `tags`）可以避免将 SubAgent 的中间输出直接流式传递给用户
- 主 agent 在收到 SubAgent 的 tool result 后生成的回复才是用户可见的流式输出
- **关键**: 当前 `agent_service.py` 中的 `on_chat_model_stream` 事件处理需要区分主 agent 和 SubAgent 的 LLM 输出。可通过 `event.get("tags")` 或 `event.get("metadata")` 过滤

### R-004: SubAgent 超时控制

**Decision**: 在 SubAgent tool 函数内部使用 `asyncio.timeout(60)` 包裹 `ainvoke` 调用。LLM 级重试（LLMConnectionError/LLMTimeoutError/LLMInvalidResponseError）由 ChatOpenAI `max_retries=3` 内部处理（与当前主 agent 行为一致）。`run_subagent` 需显式捕获以下异常并返回差异化用户提示：`asyncio.TimeoutError`（超时）、`LLMRateLimitError`（不重试，返回等待提示）、`LLMContentFilterError`（不重试，提示修改内容）、`LLMQuotaExceededError`（不重试，提示联系管理员）。通用 `Exception` 作为兜底，返回友好错误文本给主 agent。

**Rationale**: 统一 60 秒超时，与规范一致。使用 Python 原生的 `asyncio.timeout` 而非 LangGraph 的超时机制，更简单可控。异常分类策略对齐宪法 4.3 LLM 异常处理要求。

### R-005: 监控面板兼容性

**Decision**: SubAgent 内部的工具调用事件会通过 `astream_events` 冒泡到主 agent 的事件流中。`agent_service.py` 中的 `on_tool_end` 处理器无需区分是直接工具还是 SubAgent 内部工具 — 它只需累加 token 计数。但需注意：

1. SubAgent 自身作为 tool 被调用时，也会产生 `on_tool_end` 事件，其 output 是 SubAgent 的完整结果文本
2. SubAgent 内部工具的 `on_tool_end` 也会冒泡出来

**方案**: 在 `tool_processes` 追踪中记录所有 `on_tool_end` 事件（包括 SubAgent 级和内部工具级），不做过滤。监控面板将展示更细粒度的工具调用链路。

### R-006: prompt 模板精简策略

**Decision**: 将 `tool_usage.j2` 中的具体工具使用指南（记忆工具4个操作的详细说明、搜索工具引用规范、Python 执行规范）移入各 SubAgent 的内部 system prompt。主 agent 的 `tool_usage.j2` 仅保留通用工具使用原则（基本原则 + 调用规范），不再包含具体工具说明。

**Token 减少估算**:
- 当前 `tool_usage.j2` 约 85 行，估算 ~1200 tokens
- 精简后主 agent 仅保留通用原则 ~15 行，约 ~200 tokens
- 减少 ~83%，满足 SC-005 (>50%)

**Rationale**: 每个 SubAgent 的详细工具使用指南嵌入其内部 prompt，不占用主 agent 的 context window。主 agent 只需理解 SubAgent 的能力边界即可。

### R-007: 公共工具注入策略

**Decision**: 在 `base.py` 中定义 `get_common_tools()` 函数，返回 `[mem_search, web_search]`（web_search 受 `BRAVE_SEARCH_API_KEY` 条件控制）。`run_subagent()` 内部自动将公共工具与专属工具合并后传给 `create_react_agent`，按工具名去重避免重复注册（如 search_subagent 已有 web_search）。

**Rationale**:
- 公共工具由 base.py 统一管理，新增 SubAgent 自动获得公共能力，符合 SC-004 扩展性目标
- mem_search 提供只读记忆查询，让所有 SubAgent 可感知用户上下文并自主决策
- web_search 提供网络搜索，让代码执行等场景可查阅文档、获取实时数据
- SubAgent 应尽可能自主完成任务，遇到信息不足时主动使用公共工具补充，而非返回不完整结果给主 agent
- 去重处理确保 search_subagent 不会出现两个 web_search 工具

---

## Phase 1: Design & Contracts

### 1.1 SubAgent 架构设计

#### 核心模式: SubAgent = @tool 装饰的异步函数

```python
# 伪代码示意
@tool
async def search_subagent(task: str, config: RunnableConfig) -> str:
    """搜索互联网获取最新信息。当用户需要实时信息时使用此工具。"""
    user_id = _get_user_id(config)

    # 创建内部 react agent
    llm = await get_llm()
    # 合并公共工具（去重）
    common = get_common_tools()
    all_tools = list({t.name: t for t in common + SEARCH_TOOLS}.values())
    agent = create_react_agent(model=llm, tools=all_tools, prompt=SEARCH_PROMPT)

    # 执行（带超时）
    async with asyncio.timeout(SUBAGENT_TIMEOUT):
        result = await agent.ainvoke(
            {"messages": [HumanMessage(content=task)]},
            config={"configurable": {"user_id": user_id}},
        )

    # 提取最终回复
    return result["messages"][-1].content
```

#### SubAgent 注册表模式

```python
# subagents/__init__.py
def get_subagent_tools() -> list:
    """根据配置条件组装可用的 subagent 工具列表"""
    tools = []

    # 搜索 SubAgent：需要 BRAVE_SEARCH_API_KEY
    if settings.BRAVE_SEARCH_API_KEY:
        from .search_agent import search_subagent
        tools.append(search_subagent)

    # 记忆 SubAgent：始终启用
    from .memory_agent import memory_subagent
    tools.append(memory_subagent)

    # 代码执行 SubAgent：始终启用
    from .code_agent import code_subagent
    tools.append(code_subagent)

    return tools
```

### 1.2 SubAgent 接口定义

#### 搜索 SubAgent

| 属性 | 值 |
|------|------|
| **tool 名称** | `search_subagent` |
| **tool 描述** | "搜索互联网获取最新信息。当用户需要实时资讯（新闻、天气、股价、技术动态等）或需要查找特定网址、文档时使用。" |
| **参数** | `task: str` — 主 agent 提炼的搜索任务描述 |
| **专属工具** | `web_search` (代码中通过 `SEARCH_TOOLS` 列表导入，来自 `tools/search.py`) |
| **公共工具** | `mem_search` (由 `base.py` 自动注入，去重处理) |
| **内部 prompt** | 搜索结果整合 + 引用规范 + 自主使用 mem_search 补充用户上下文 |
| **条件启用** | `settings.BRAVE_SEARCH_API_KEY` 存在 |
| **超时** | 60 秒 |

#### 记忆 SubAgent

| 属性 | 值 |
|------|------|
| **tool 名称** | `memory_subagent` |
| **tool 描述** | "管理用户的长期记忆。当用户要求记住、回忆、更新或删除个人信息时使用。" |
| **参数** | `task: str` — 主 agent 提炼的记忆操作任务描述 |
| **专属工具** | `mem_search`, `mem_cache`, `mem_update`, `mem_delete` (来自 `tools/memory.py`) |
| **公共工具** | `web_search` (由 `base.py` 自动注入，mem_search 已在专属工具中，去重跳过) |
| **内部 prompt** | 记忆操作指南（去重、更新 vs 删除决策、内容精炼要求）+ 可使用 web_search 验证信息 |
| **条件启用** | 始终启用 |
| **超时** | 60 秒 |

#### 代码执行 SubAgent

| 属性 | 值 |
|------|------|
| **tool 名称** | `code_subagent` |
| **tool 描述** | "执行 Python 代码进行计算、数据处理或验证。当用户需要数学计算、统计分析、数据转换或明确要求运行代码时使用。" |
| **参数** | `task: str` — 主 agent 提炼的代码执行任务描述 |
| **专属工具** | `python_exec` (来自 `tools/python_repl.py`) |
| **公共工具** | `mem_search` + `web_search` (由 `base.py` 自动注入) |
| **内部 prompt** | Python 执行规范 + 自主使用 mem_search 获取上下文、web_search 查阅文档 |
| **条件启用** | 始终启用 |
| **超时** | 60 秒 |

### 1.3 agent.py 变更设计

#### create_chat_agent 重构

```python
# 重构前
@asynccontextmanager
async def create_chat_agent(prompt=None, extra_tools=None, ...):
    from apps.graph.tools.memory import MEMORY_TOOLS
    from apps.graph.tools.python_repl import REPL_TOOLS
    from apps.graph.tools.search import SEARCH_TOOLS
    async with _create_agent(
        list(MEMORY_TOOLS) + list(SEARCH_TOOLS) + list(REPL_TOOLS) + (extra_tools or []),
        prompt, preamble_tokens, effective_window, use_checkpointer=False,
    ) as agent:
        yield agent

# 重构后
@asynccontextmanager
async def create_chat_agent(prompt=None, extra_tools=None, ...):
    from apps.graph.subagents import get_subagent_tools
    subagent_tools = get_subagent_tools()
    async with _create_agent(
        subagent_tools + (extra_tools or []),
        prompt, preamble_tokens, effective_window, use_checkpointer=False,
    ) as agent:
        yield agent
```

### 1.4 agent_service.py 事件过滤设计

SubAgent 内部的 LLM 调用会产生 `on_chat_model_stream` 事件。需要在事件处理中过滤，只将主 agent 的 LLM 输出流式传递给用户。

**过滤策略**: 利用 `astream_events` 的 `tags` 机制或检查事件的 `run_id` 层级。

```python
# 方案：通过 tags 过滤
# 在 agent_service.py 的 execute()/resume() 中，调用 astream_events 前设置 tags
# （tags 设置在调用端 agent_service.py，因为 config 在 get_agent_config() 中创建）
config["tags"] = ["main_agent"]

# 在事件处理中
if event["event"] == "on_chat_model_stream":
    tags = event.get("tags", [])
    # 只处理主 agent 的 LLM 输出
    if "main_agent" not in tags:
        continue  # 跳过 SubAgent 的 LLM 输出
```

**备选方案**: 如果 tags 无法可靠区分，可通过记录主 agent 的 `run_id`（首次 `on_chain_start` 事件），只处理该 `run_id` 下的 `on_chat_model_stream` 事件。

### 1.5 tool_usage.j2 精简设计

**精简后内容（主 agent 可见）**:

```
# 工具使用

## 基本原则
- 当你判断使用工具能更好地解决用户的问题时，主动调用工具，无需征求许可。
- 优先使用最直接、最高效的工具。
- 一次回复中可以调用多个工具。如果多个工具调用之间没有依赖关系，应并行调用。
- 如果工具调用失败，分析错误原因并向用户说明。

## 调用规范
- 向工具传递清晰、完整的任务描述，确保工具能独立理解并完成任务。
- 工具返回的结果应当被合理地整合到回复中，而不是原样展示。
- 如果工具结果与用户期望不符，说明情况并提供解释。
```

移除的内容（移入各 SubAgent 内部 prompt）:
- 记忆工具使用指南（~40 行）
- 网络搜索工具（~15 行）
- Python 代码执行工具（~12 行）

### 1.6 SubAgent 内部 Prompt 设计

各 SubAgent 的内部 system prompt 包含从 `tool_usage.j2` 移出的对应工具使用指南，以及公共工具的自主使用策略。**核心原则：SubAgent 应尽可能自主完成任务，遇到信息不足时主动使用公共工具补充，而非返回不完整结果给主 agent。**

**搜索 SubAgent prompt**:
```
你是搜索助手。根据任务描述搜索互联网获取信息。

## 执行策略
- 搜索前先用 mem_search 查询用户记忆，了解用户背景以优化搜索策略
- 使用 web_search 工具进行搜索
- 搜索结果按编号返回，整合时用 [[N]] 标注引用来源
- 回答末尾附上引文列表
- 如果首次搜索无结果，调整关键词重新搜索
- 独立完成任务，返回完整的搜索整合结果
```

**记忆 SubAgent prompt**:
```
你是记忆管理助手。根据任务描述管理用户的长期记忆。

## 工具
- mem_search: 搜索记忆，返回 [id=<memory_id>] <内容>
- mem_cache: 保存新记忆
- mem_update: 更新记忆（需要 memory_id）
- mem_delete: 删除记忆（需要 memory_id）

## 执行策略
- 保存前必须先 mem_search 搜索去重
- 保存内容应为精炼的事实性信息
- 更新/删除前必须先搜索获取 memory_id
- 如需验证信息准确性，可使用 web_search 搜索确认
- 独立完成任务，返回操作结果和确认信息
```

**代码执行 SubAgent prompt**:
```
你是代码执行助手。根据任务描述编写并执行 Python 代码。

## 执行策略
- 编写代码前，主动用 mem_search 查询用户记忆，获取可能相关的上下文
  （如用户偏好、之前提到的数据、特定需求等）
- 如果任务涉及实时数据或不确定的信息，主动用 web_search 查询
- 使用 python_exec 工具执行代码
- 使用 print() 输出结果
- 执行失败时分析错误，可通过 web_search 查找解决方案后修正代码重试
- 返回关键代码和执行结果
- 独立完成任务，避免返回不完整的结果
```

### 1.7 监控数据兼容性

SubAgent 执行产生的事件在 `astream_events` 中的表现：

| 事件 | 来源 | 处理方式 |
|------|------|----------|
| `on_chat_model_stream` (主 agent) | 主 agent LLM | 流式输出给用户 ✅ |
| `on_chat_model_stream` (SubAgent) | SubAgent 内部 LLM | 过滤，不输出 |
| `on_chat_model_end` (主 agent) | 主 agent LLM | 提取 token 用量 ✅ |
| `on_chat_model_end` (SubAgent) | SubAgent 内部 LLM | 提取 token 用量（累加） ✅ |
| `on_tool_end` (SubAgent 工具) | SubAgent 被主 agent 调用 | 记录 tool_processes ✅ |
| `on_tool_end` (内部工具) | SubAgent 内部工具调用 | 记录 tool_processes ✅ |

**关键决策**: `on_chat_model_end` 事件的 token 统计无需区分来源，全部累加到 `total_prompt_tokens` / `total_completion_tokens`，这正好反映了总消耗。

---

## Implementation Plan (高层步骤)

### Step 1: 创建 SubAgent 基础设施
- 新建 `backend/apps/graph/subagents/` 目录
- 实现 `base.py` — SubAgent 工厂函数（创建内部 react agent + 超时包裹）
- 实现 `__init__.py` — 条件注册表

### Step 2: 实现 3 个 SubAgent
- `search_agent.py` — 搜索 SubAgent（包装 SEARCH_TOOLS）
- `memory_agent.py` — 记忆 SubAgent（包装 MEMORY_TOOLS）
- `code_agent.py` — 代码执行 SubAgent（包装 REPL_TOOLS）

### Step 3: 重构 agent.py
- `create_chat_agent` 改为使用 `get_subagent_tools()` 代替直接引用工具列表

### Step 4: 适配 agent_service.py
- 过滤 SubAgent 内部的 `on_chat_model_stream` 事件，仅流式输出主 agent 的 LLM 内容
- 确保 `on_tool_end` 和 `on_chat_model_end` 的 token 统计正常

### Step 5: 精简 tool_usage.j2
- 移除具体工具使用指南，仅保留通用原则
- 各 SubAgent 的内部 prompt 包含原工具使用指南

### Step 6: 更新 graph.py（独立调试模式）
- 适配 `chat_graph` 使用 SubAgent 工具

### Step 7: 测试
- SubAgent 单元测试（mock LLM + mock 工具）
- 集成测试（验证主 agent → SubAgent → 工具 完整链路）
- 监控兼容性测试（验证 token 统计和 tool_processes 追踪）
- 回归测试（普通对话、搜索、记忆、代码执行）

---

## Risk Assessment

| 风险 | 概率 | 影响 | 缓解措施 |
|------|------|------|----------|
| SubAgent 内部 LLM 输出被误流式传递给用户 | 中 | 高 | 通过 tags/run_id 严格过滤 `on_chat_model_stream` |
| SubAgent 增加一层 LLM 调用导致延迟退化 | 中 | 中 | 监控对比重构前后 p95 延迟，必要时优化 SubAgent prompt 长度 |
| 复合任务中 SubAgent 调用顺序不当 | 低 | 中 | 依赖 LLM 的 react agent 循环自行决定，与当前多工具模式一致 |
| token 统计重复计数 | 低 | 低 | 验证 `on_chat_model_end` 事件不会重复触发 |
