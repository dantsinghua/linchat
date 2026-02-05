# Tasks: 主对话流程 SubAgent 化重构

**Input**: Design documents from `/specs/006-subagent-tools/`
**Prerequisites**: plan.md (required), spec.md (required), research.md, data-model.md, quickstart.md

**Organization**: Tasks are grouped by user story to enable independent implementation and testing of each story.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3)
- Include exact file paths in descriptions

---

## Phase 1: Setup (SubAgent 基础设施)

**Purpose**: 创建 SubAgent 模块骨架和公共工厂函数

- [X] T001 创建 `backend/apps/graph/subagents/` 目录结构，含空的 `__init__.py`。同时创建 `backend/apps/graph/subagents/README.md`，说明 SubAgent 模块的职责（封装各类 SubAgent 定义和注册逻辑）、使用方式（通过 `get_subagent_tools()` 获取可用 SubAgent 列表）和扩展指南（新增 SubAgent 仅需添加定义文件 + 注册），满足宪法第七条模块文档要求
- [X] T002 实现 SubAgent 工厂函数 `run_subagent()` 在 `backend/apps/graph/subagents/base.py` 中。该函数接收 `task: str, config: RunnableConfig, tools: list, prompt: str, llm: Optional = None`（llm 默认 None 时调用 `get_llm()` 获取 LLM 实例，T023 将填充降级获取逻辑），内部创建 `create_react_agent` + `asyncio.timeout(60)` 包裹的 `ainvoke` 调用，从 config 提取 `user_id` 传递给内部 agent，提取 `result["messages"][-1].content` 作为返回值。参考 `backend/apps/graph/agent.py` 中的 `get_llm()` 获取 LLM 实例。LLM 级别的异常重试（LLMConnectionError/LLMTimeoutError/LLMInvalidResponseError 重试 3 次）由 LangChain ChatOpenAI 的 max_retries 参数处理（与当前主 agent 行为一致）。`run_subagent()` 需显式捕获以下异常并返回差异化用户提示：(1) `asyncio.TimeoutError` → "该操作执行超时（60秒），请稍后重试"；(2) `LLMRateLimitError` → "请求过于频繁，请等待后重试"（不重试）；(3) `LLMContentFilterError` → "消息内容可能包含敏感信息，请修改后重试"（不重试）；(4) `LLMQuotaExceededError` → "服务配额已用尽，请联系管理员"（不重试）；(5) 其他 `Exception` → "服务暂时不可用，请稍后重试"（兜底）。确保 SubAgent 异常不会冒泡中断主 agent 执行。LLM 实例获取策略：每次 `run_subagent` 调用独立获取 LLM（调用 `get_llm()`），与当前主 agent 行为一致；虽然复合任务中可能产生多次 DB 查询，但保证 SubAgent 始终使用最新模型配置，且 DB 查询开销相比 LLM 调用可忽略。同时实现 `get_common_tools()` 函数，返回公共工具列表 `[mem_search, web_search]`（web_search 受 `settings.BRAVE_SEARCH_API_KEY` 条件控制）。`run_subagent()` 内部自动将 `get_common_tools()` 返回值与传入的 `tools` 参数合并，按工具名去重避免重复注册。从 `apps.graph.tools.memory` 直接导入模块级函数 `mem_search`（`from apps.graph.tools.memory import mem_search`），从 `apps.graph.tools.search` 直接导入 `web_search`（`from apps.graph.tools.search import web_search`）。注意：这两个函数未在各自模块的导出列表（MEMORY_TOOLS/SEARCH_TOOLS）中单独声明，但作为模块级 `@tool` 函数可直接按名导入。去重逻辑按 `tool.name` 属性匹配
- [X] T003 实现 SubAgent 注册表骨架 `get_subagent_tools()` 在 `backend/apps/graph/subagents/__init__.py` 中。创建函数框架，返回空列表。具体 SubAgent 的注册在 T010/T013/T016 中完成
- [X] T003.5 [Spike] 验证 tags 过滤机制在嵌套 agent 中的传播行为。创建最小化测试脚本：(1) 用 `create_react_agent` 创建主 agent，绑定一个 `@tool` 函数（内部再创建一个 `create_react_agent` 并 `ainvoke`）；(2) 在主 agent 的 `astream_events` 调用中设置 `config["tags"] = ["main_agent"]`；(3) 观察主 agent 和内部 agent 的 `on_chat_model_stream` 事件是否携带不同 tags；(4) 记录验证结论，确定 T005 最终采用的过滤方案（config tags / include_tags / run_id）。此任务为 spike，验证脚本不合入主代码

**Checkpoint**: SubAgent 基础设施就绪，tags 过滤方案已验证，可开始实现具体 SubAgent

---

## Phase 2: Foundational (主 Agent 重构 + 事件过滤)

**Purpose**: 重构主 agent 使用 SubAgent 工具列表，适配流式事件过滤

**⚠️ CRITICAL**: 此阶段完成后才能正确运行所有 User Story

- [X] T004 重构 `create_chat_agent()` 在 `backend/apps/graph/agent.py` 中。将工具列表从 `list(MEMORY_TOOLS) + list(SEARCH_TOOLS) + list(REPL_TOOLS)` 改为 `get_subagent_tools()`，移除对 `apps.graph.tools.memory/search/python_repl` 的直接导入，改为导入 `apps.graph.subagents.get_subagent_tools`。保留 `extra_tools` 参数和 `use_checkpointer=False` 不变
- [X] T005 适配 `agent_service.py` 中的 `on_chat_model_stream` 事件过滤，在 `backend/apps/graph/services/agent_service.py` 中。SubAgent 内部 LLM 会产生 `on_chat_model_stream` 事件，需过滤以避免将 SubAgent 中间输出误当作最终回复流式传递给用户。方案（已选定）：使用 tags 机制过滤——在 `agent_service.py` 的 `execute()` 和 `resume()` 方法中，调用 `astream_events` 前通过 `config["tags"] = ["main_agent"]` 给主 agent 运行打标签（tags 设置在调用端 agent_service.py 而非定义端 agent.py，因为 config 在 `get_agent_config()` 中创建），在事件处理中仅当 `on_chat_model_stream` 事件的 tags 包含 `"main_agent"` 时才处理为用户输出。备选方案（仅在 tags 不可靠时启用）：记录主 agent 首个 `on_chain_start` 事件的 `run_id`，仅处理该 `run_id` 直接关联的 `on_chat_model_stream` 事件。`on_tool_end` 和 `on_chat_model_end` 事件不过滤，全部累加 token 统计。**同时适配 `resume()` 方法中的 `astream_events` 事件处理循环中的 `on_chat_model_stream` 事件过滤，使用与 `execute()` 相同的过滤策略。** 注意：`resume()` 方法当前无监控逻辑（无 breakdown/tool_processes/on_tool_end/on_chat_model_end 处理），这是预期行为——`resume()` 是轻量级的继续生成流程。但 SubAgent 化后 `resume()` 仍可能触发 SubAgent 调用（LLM 判断"请继续"需要工具时），因此 `on_chat_model_stream` 事件过滤是必须的，否则 SubAgent 内部 LLM 输出会被误当作最终回复流式传递给用户。`resume()` 中不添加 token 统计和监控推送属于可接受的功能缺失（继续生成是低频操作）。 实施前先验证 tags 过滤可行性：(1) 在 `agent_service.py` 的 `execute()/resume()` 中，调用 `astream_events` 前通过 `config["tags"] = ["main_agent"]` 打标签；(2) 运行一次包含 SubAgent 调用的请求，检查 `astream_events` 输出中主 agent 的 `on_chat_model_stream` 事件是否携带 `"main_agent"` tag，SubAgent 内部的 `on_chat_model_stream` 事件是否不携带该 tag；(3) 若 `config["tags"]` 未能自动传播到子事件（即所有事件都携带该 tag 或都不携带），则改用 `astream_events` 的 `include_tags=["main_agent"]` 参数直接在事件源头过滤——该参数让 `astream_events` 仅产出匹配指定 tags 的事件，需要配合在 `create_chat_agent` 的 `create_react_agent` 调用中显式设置 LLM 的 tags（如 `llm.with_config(tags=["main_agent"])`）；(4) 若以上方案均不可靠，切换到 run_id 备选方案：记录主 agent 首个 `on_chain_start` 事件的 `run_id`，仅处理该 `run_id` 直接关联的 `on_chat_model_stream` 事件
- [X] T006 精简 `tool_usage.j2` 在 `backend/apps/context/templates/tool_usage.j2` 中。移除"记忆工具使用指南"（第16-54行）、"网络搜索工具"（第56-70行）、"Python 代码执行工具"（第72-85行），仅保留"基本原则"和"调用规范"两节。修改"调用规范"中的描述，增加"向工具传递清晰、完整的任务描述，确保工具能独立理解并完成任务"和"你的工具是专业助手，向它们传递完整的任务上下文，它们会独立完成任务并返回结果"。确认 `backend/apps/context/builder.py:L367` 的 `TOOL_USAGE_GUIDELINES` 兼容常量在精简后仍可正常渲染，检查引用该常量的测试是否需要同步更新

**Checkpoint**: 主 Agent 框架重构完成，但因 SubAgent 尚未实现，功能暂不可用

---

## Phase 3: User Story 1 — 普通对话消息 (Priority: P1) 🎯 MVP

**Goal**: 确保重构后普通对话（不涉及任何工具）正常工作，响应时间无退化

**Independent Test**: 发送"你好"，验证 AI 正常回复且响应时间与重构前一致

### Implementation for User Story 1

- [X] T007 [US1] 验证重构后普通对话功能：确认 `create_chat_agent` 重构后 `create_react_agent` 能正确处理 SubAgent 工具列表（当 LLM 判断不需要工具时仍直接回复）。启动后端 `uvicorn core.asgi:application --host 0.0.0.0 --port 8002 --reload`，通过聊天界面发送"你好"，验证 AI 正常回复。验证发送多条普通消息时上下文保持连贯

**Checkpoint**: 普通对话功能正常，基础对话能力未被重构破坏

---

## Phase 4: User Story 2 — 搜索类任务 (Priority: P1)

**Goal**: 用户发送搜索请求，主 agent 委派给搜索 SubAgent，SubAgent 内部调用 web_search 工具，结果返回主 agent 整合后回复

**Independent Test**: 发送"搜索 2026 年春节是几号"，验证返回包含搜索结果和引用来源的回复

### Implementation for User Story 2

- [X] T009 [P] [US2] 实现搜索 SubAgent 在 `backend/apps/graph/subagents/search_agent.py` 中。定义 `SEARCH_PROMPT` 常量（搜索助手 system prompt，包含：搜索前先用 mem_search 查询用户记忆了解背景以优化搜索策略、使用 web_search 搜索、`[[N]]` 引用规范、回答末尾附引文列表、首次无结果时调整关键词重新搜索、独立完成任务返回完整结果。PROMPT 内容必须包含 plan.md 1.6 节设计稿中的所有要素，特别是公共工具自主使用策略——SubAgent 应在执行前主动使用 mem_search 补充用户上下文，而非返回不完整结果）。定义 `@tool` 装饰的 `search_subagent(task: str, config: RunnableConfig) -> str` 函数，docstring 为"搜索互联网获取最新信息。当用户需要实时资讯（新闻、天气、股价、技术动态等）或需要查找特定网址、文档时使用。"，内部调用 `run_subagent(task, config, SEARCH_TOOLS, SEARCH_PROMPT)`。从 `apps.graph.tools.search` 导入 `SEARCH_TOOLS`。注意：公共工具 mem_search 由 `run_subagent()` 自动注入，无需在此显式添加
- [X] T010 [US2] 在 `backend/apps/graph/subagents/__init__.py` 中注册搜索 SubAgent：在 `get_subagent_tools()` 函数中，当 `settings.BRAVE_SEARCH_API_KEY` 为 truthy 时，导入并添加 `search_subagent` 到工具列表
- [X] T011 [US2] 端到端验证搜索功能：启动后端，通过聊天界面发送"搜索今天黄金价格"，验证 AI 返回包含搜索结果和引用来源的回复。确认用户体验与重构前一致（结果整合、引文列表）

**Checkpoint**: 搜索 SubAgent 功能正常，搜索任务委派链路通畅

---

## Phase 5: User Story 3 — 代码执行类任务 (Priority: P1)

**Goal**: 用户发送代码执行请求，主 agent 委派给代码 SubAgent，SubAgent 内部调用 python_exec 工具

**Independent Test**: 发送"用 Python 计算斐波那契数列前 10 项"，验证返回正确结果

### Implementation for User Story 3

- [X] T012 [P] [US3] 实现代码执行 SubAgent 在 `backend/apps/graph/subagents/code_agent.py` 中。定义 `CODE_PROMPT` 常量（代码执行助手 system prompt，包含：编写代码前主动用 mem_search 查询用户记忆获取上下文、涉及实时数据时主动用 web_search 查询、使用 python_exec 执行代码、使用 print() 输出、失败时分析错误可通过 web_search 查找解决方案后修正重试、返回关键代码和结果、独立完成任务避免返回不完整结果。PROMPT 内容必须包含 plan.md 1.6 节设计稿中的所有要素，特别是公共工具自主使用策略——SubAgent 应在执行前主动使用 mem_search/web_search 补充上下文）。定义 `@tool` 装饰的 `code_subagent(task: str, config: RunnableConfig) -> str` 函数，docstring 为"执行 Python 代码进行计算、数据处理或验证。当用户需要数学计算、统计分析、数据转换或明确要求运行代码时使用。"，内部调用 `run_subagent(task, config, REPL_TOOLS, CODE_PROMPT)`。从 `apps.graph.tools.python_repl` 导入 `REPL_TOOLS`。注意：公共工具 mem_search + web_search 由 `run_subagent()` 自动注入
- [X] T013 [US3] 在 `backend/apps/graph/subagents/__init__.py` 中注册代码执行 SubAgent：在 `get_subagent_tools()` 中无条件添加 `code_subagent`
- [X] T014 [US3] 端到端验证代码执行功能：发送"用 Python 计算圆周率前 50 位"，验证返回正确结果。发送一条会导致代码执行出错的请求，验证 SubAgent 能自行重试或返回错误说明

**Checkpoint**: 代码执行 SubAgent 功能正常

---

## Phase 6: User Story 4 — 记忆操作 (Priority: P1)

**Goal**: 用户发送记忆相关请求，主 agent 委派给记忆 SubAgent，SubAgent 内部调用 mem_* 工具

**Independent Test**: 发送"记住我喜欢蓝色"，然后发送"我喜欢什么颜色"，验证记忆存取正常

### Implementation for User Story 4

- [X] T015 [P] [US4] 实现记忆 SubAgent 在 `backend/apps/graph/subagents/memory_agent.py` 中。定义 `MEMORY_PROMPT` 常量（记忆管理助手 system prompt，包含：4 个工具说明（mem_search/mem_cache/mem_update/mem_delete）、保存前必须先 mem_search 搜索去重、保存内容应为精炼事实性信息、更新/删除前必须先搜索获取 memory_id、如需验证信息准确性可使用 web_search 搜索确认、独立完成任务返回操作结果和确认信息。从 `tool_usage.j2` 原"记忆工具使用指南"节迁移并增强。PROMPT 内容必须包含 plan.md 1.6 节设计稿中的所有要素，特别是公共工具自主使用策略——SubAgent 可使用 web_search 验证信息准确性）。定义 `@tool` 装饰的 `memory_subagent(task: str, config: RunnableConfig) -> str` 函数，docstring 为"管理用户的长期记忆。当用户要求记住、回忆、更新或删除个人信息时使用。"，内部调用 `run_subagent(task, config, MEMORY_TOOLS, MEMORY_PROMPT)`。从 `apps.graph.tools.memory` 导入 `MEMORY_TOOLS`。注意：公共工具 web_search 由 `run_subagent()` 自动注入（mem_search 已在 MEMORY_TOOLS 中，去重跳过）
- [X] T016 [US4] 在 `backend/apps/graph/subagents/__init__.py` 中注册记忆 SubAgent：在 `get_subagent_tools()` 中无条件添加 `memory_subagent`
- [X] T017 [US4] 端到端验证记忆功能：发送"记住我最喜欢吃火锅"，验证保存确认。发送"我喜欢吃什么"，验证返回正确记忆内容。测试记忆更新和删除操作

**Checkpoint**: 记忆 SubAgent 功能正常，记忆读写链路通畅

---

## Phase 7: User Story 5 — 复合任务 (Priority: P2)

**Goal**: 用户发送涉及多个 SubAgent 的复合任务，主 agent 通过 react agent 多轮循环依次委派

**Independent Test**: 发送"搜索美元兑人民币汇率，然后用 Python 计算 1 万美元等于多少人民币"

### Implementation for User Story 5

- [X] T018 [US5] 端到端验证复合任务：发送"搜索美元兑人民币汇率，然后用 Python 计算 1 万美元等于多少人民币"，验证搜索 SubAgent 和代码 SubAgent 依次被调用，最终结果正确。验证当其中一个 SubAgent 执行失败时，主 agent 能说明哪部分失败，已完成部分正常展示

**Checkpoint**: 复合任务功能正常，多 SubAgent 协作链路通畅

---

## Phase 8: User Story 6 — 新工具类型扩展 (Priority: P3)

**Goal**: 验证新增 SubAgent 的扩展性——只需 2 个文件（定义 + 注册），不修改主 agent 核心逻辑

**Independent Test**: 添加一个 mock SubAgent，验证主 agent 能发现并委派任务

### Implementation for User Story 6

- [X] T019 [US6] 验证扩展性：确认 SC-004（新增 SubAgent 修改文件数 ≤ 2）。检查从 Phase 4-6 的实现过程，确认每个 SubAgent 仅涉及 1 个定义文件 + `__init__.py` 注册，`agent.py` 核心逻辑未被修改
- [X] T020 [US6] 验证条件注册：在 `.env` 中临时移除 `BRAVE_SEARCH_API_KEY`，重启后端，验证搜索 SubAgent 未注册但记忆和代码 SubAgent 正常工作。恢复配置后搜索 SubAgent 重新启用

**Checkpoint**: 扩展性验证通过

---

## Phase 9: Polish & Cross-Cutting Concerns

**Purpose**: 监控兼容、独立调试模式、代码质量

- [X] T021 验证上下文监控面板兼容性（FR-008 / SC-006）。发送搜索/代码/记忆任务，按以下标准逐项验证：(1) `tool_processes` 列表中 SubAgent 自身的 `on_tool_end` 事件 `name` 字段为 SubAgent 工具名（如 `search_subagent`），(2) SubAgent 内部工具的 `on_tool_end` 事件 `name` 字段为具体工具名（如 `web_search`、`python_exec`、`mem_search`），(3) 两层事件都出现在 `tool_processes` 中（即一次搜索任务至少产生 2 条记录：`search_subagent` + `web_search`），(4) `total_prompt_tokens` 和 `total_completion_tokens` 同时包含主 agent 和 SubAgent 内部 LLM 的 token 消耗（验证方式：对比监控面板显示值与 Langfuse trace 中的总 token 数），(5) 监控面板实时刷新频率与重构前一致（500ms 间隔）
- [X] T022 [P] 验证 SC-005（prompt token 减少 ≥ 50%）。对比重构前后 `tool_usage.j2` 的 token 数：使用 `apps.common.tokenizer.count_tokens()` 分别计算原版和精简版的 token 数，确认减少比例
- [X] T023 [P] 更新 `backend/apps/graph/graph.py` 独立调试模式。将 `chat_graph` 的工具列表从 `list(MEMORY_TOOLS)` 改为使用 `get_subagent_tools()`，使调试模式下的 chat_graph 与生产环境 create_chat_agent 保持一致的工具配置。其他 graph 定义（context_graph / memory_graph / cronmem_graph）保持不变。由于独立模式下无 Django 环境，`run_subagent()` 中的 `get_llm()` 不可用——需在 `base.py` 的 `run_subagent()` 中填充 `llm` 参数的降级获取逻辑（T002 已预留接口）：优先调用 `get_llm()`（Django 模式），失败时降级为从环境变量创建 LLM 实例（复用 `graph.py` 中 `_get_llm()` 的逻辑）
- [X] T024 [P] 添加 `SUBAGENT_TIMEOUT` 配置项到 `backend/core/settings.py` 中，默认值 60 秒，从 `backend/apps/graph/subagents/base.py` 中引用此配置
- [X] T025 [P] 编写 SubAgent 单元测试在 `backend/tests/apps/graph/test_subagents.py` 中。测试内容：(1) `run_subagent` 工厂函数正常执行和超时处理，以及 LLMRateLimitError/LLMContentFilterError/LLMQuotaExceededError 的差异化错误提示；(2) `get_common_tools()` 公共工具列表正确性（含/不含 BRAVE_SEARCH_API_KEY）及去重逻辑验证；(3) 各 SubAgent tool 函数（search/memory/code）mock LLM + mock 内部工具，验证入参传递和结果提取，验证公共工具已自动注入（如 code_subagent 内部 react agent 的工具列表包含 mem_search + web_search）；(4) `get_subagent_tools()` 条件注册逻辑（有/无 `BRAVE_SEARCH_API_KEY`）；(5) agent_service 事件过滤兼容性测试：构造模拟的 astream_events 事件序列（包含主 agent 和 SubAgent 的 on_chat_model_stream 事件，通过不同 tags 区分），验证只有主 agent 的 stream 事件被处理为用户输出，验证所有 on_chat_model_end 事件的 token 统计被正确累加（不区分来源），验证 on_tool_end 事件同时记录 SubAgent 级和内部工具级调用；(6) SubAgent 端到端模拟测试：构造 10 组不同意图的用户消息（3 搜索 + 3 代码 + 3 记忆 + 1 普通对话），mock LLM 返回可预测的 tool_call 决策和结果，验证所有场景正确委派且返回非空结果（覆盖 SC-002 自动化验证基准）；(7) Edge Case 测试：(a) mock LLM 不调用任何 SubAgent 工具直接生成回复，验证主 agent 正常输出且无 SubAgent 相关事件（覆盖 spec Edge Case 4："所有 subagent 都不适用"）；(b) mock run_subagent 抛出 `asyncio.TimeoutError`，验证返回"执行超时"友好提示且主 agent 继续运行（覆盖 spec Edge Case 2："subagent 执行超时"）；(c) mock SubAgent 内部工具调用失败（如 web_search 返回错误），验证 SubAgent 自行处理错误并返回错误说明文本给主 agent，主 agent 不中断（覆盖 spec Edge Case 3："subagent 内部工具调用失败"）；(d) 构造模拟的 astream_events 事件序列，验证 SubAgent 内部 LLM 的 on_chat_model_stream 事件**不**被处理为用户可见输出，仅主 agent 的 LLM 输出对用户可见（覆盖 spec Edge Case 5："流式输出中 subagent 中间过程不可见"）
- [X] T026 [P] 编写 SubAgent 自主性行为测试在 `backend/tests/apps/graph/test_subagent_autonomy.py` 中。测试内容：(1) code_subagent 自主使用公共工具测试：mock LLM 使其在执行代码前先调用 mem_search 查询用户上下文，验证 SubAgent 内部工具调用序列包含 mem_search → python_exec（而非直接 python_exec）；(2) search_subagent 自主使用记忆测试：mock LLM 使其在搜索前先调用 mem_search 了解用户偏好，验证调用序列包含 mem_search → web_search；(3) memory_subagent 自主使用搜索测试：mock LLM 使其在保存记忆时调用 web_search 验证信息准确性；(4) SubAgent 不回传主 agent 测试：构造一个信息不足的任务，验证 SubAgent 通过公共工具自行补充信息后返回完整结果，而非返回"信息不足"类文本给主 agent；(5) 主 agent 与 SubAgent 交互边界测试：验证主 agent 仅调用 SubAgent 一次，SubAgent 内部完成所有工具调用后返回最终结果，主 agent 不会因 SubAgent 返回不完整结果而发起二次调用
- [X] T027 [P] 更新 `backend/tests/chat/test_agent.py` 适配新架构。验证 `create_chat_agent` 使用 `get_subagent_tools()` 而非直接导入工具列表
- [X] T028 验证 SC-001 性能基准（延续 T007 的功能验证，本任务聚焦量化性能对比）。重构前后分别发送 5 次"你好"普通对话，记录首 token 时间和完整响应时间，对比确认退化不超过 10%
- [X] T029 代码质量检查：对 `backend/apps/graph/subagents/` 目录下所有文件运行 `black .` + `isort .`，确保类型注解完整、Google 风格文档字符串规范。确认 `backend/apps/graph/subagents/__init__.py` 包含模块级文档字符串（描述 SubAgent 模块的职责和使用方式），满足宪法第七条模块文档要求

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 (Setup)**: 无依赖 — 立即开始
- **Phase 2 (Foundational)**: 依赖 Phase 1 完成（特别是 T003.5 spike 验证结论阻塞 T005 的过滤方案选择） — 阻塞所有 User Story
- **Phase 3 (US1 普通对话)**: 依赖 Phase 2 完成
- **Phase 4 (US2 搜索)**: 依赖 Phase 2 完成，可与 Phase 5/6 并行
- **Phase 5 (US3 代码)**: 依赖 Phase 2 完成，可与 Phase 4/6 并行
- **Phase 6 (US4 记忆)**: 依赖 Phase 2 完成，可与 Phase 4/5 并行
- **Phase 7 (US5 复合)**: 依赖 Phase 4 + 5 + 6 完成（需要所有 SubAgent 就绪）
- **Phase 8 (US6 扩展)**: 依赖 Phase 4 + 5 + 6 完成
- **Phase 9 (Polish)**: 依赖 Phase 3-8 完成

### User Story Dependencies

- **US1 (普通对话)**: 仅依赖 Phase 2 主 agent 重构 — 不依赖任何 SubAgent 实现
- **US2 (搜索)**: 仅依赖 Phase 2 + 搜索 SubAgent 自身
- **US3 (代码)**: 仅依赖 Phase 2 + 代码 SubAgent 自身
- **US4 (记忆)**: 仅依赖 Phase 2 + 记忆 SubAgent 自身
- **US5 (复合)**: 依赖 US2 + US3（需要搜索和代码 SubAgent 都就绪）
- **US6 (扩展)**: 依赖 US2-US4 完成（需验证现有 SubAgent 模式）

### Parallel Opportunities

- T009 (搜索 SubAgent) / T012 (代码 SubAgent) / T015 (记忆 SubAgent) 可并行实现（不同文件）
- T022 / T023 / T024 / T025 / T026 可并行执行
- Phase 4/5/6 完成后可并行进入 Phase 7 和 Phase 8

---

## Parallel Example: Phase 4/5/6

```bash
# 三个 SubAgent 可并行实现（不同文件，无交叉依赖）：
Task T009: "实现搜索 SubAgent 在 backend/apps/graph/subagents/search_agent.py"
Task T012: "实现代码执行 SubAgent 在 backend/apps/graph/subagents/code_agent.py"
Task T015: "实现记忆 SubAgent 在 backend/apps/graph/subagents/memory_agent.py"

# 注册任务需在对应 SubAgent 实现后执行：
Task T010: 注册搜索 SubAgent (依赖 T009)
Task T013: 注册代码 SubAgent (依赖 T012)
Task T016: 注册记忆 SubAgent (依赖 T015)
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup (T001-T003)
2. Complete Phase 2: Foundational (T004-T006)
3. Complete Phase 3: US1 普通对话 (T007)
4. **STOP and VALIDATE**: 确认普通对话未被重构破坏

### Incremental Delivery

1. Setup + Foundational → 框架就绪
2. 并行实现 3 个 SubAgent (T009/T012/T015) → 注册 (T010/T013/T016)
3. 分别验证搜索/代码/记忆 (T011/T014/T017) → 各 SubAgent 独立可用
4. 验证复合任务 (T018) → 多 SubAgent 协作
5. 验证扩展性 (T019-T020) → 架构目标达成
6. Polish (T021-T029) → 生产就绪

---

## Notes

- [P] tasks = different files, no dependencies
- [Story] label maps task to specific user story for traceability
- 本特性为纯后端重构，无前端变更
- 现有工具代码（`tools/search.py`, `tools/memory.py`, `tools/python_repl.py`）不修改
- 重构完成后应运行完整回归测试，确保所有现有功能正常
- 每个任务完成后对变更文件运行 `black .` + `isort .`，确保代码风格一致
