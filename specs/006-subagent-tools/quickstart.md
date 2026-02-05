# Quickstart: 主对话流程 SubAgent 化重构

**Feature**: 006-subagent-tools
**Date**: 2026-02-05

---

## 前置条件

```bash
# 激活虚拟环境
source /home/dantsinghua/work/linchat/linchat/bin/activate

# 确认 Docker 服务运行
cd /home/dantsinghua/work/linchat
docker compose ps  # PostgreSQL, Redis 必须运行
```

---

## 开发步骤

### 1. 切换到特性分支

```bash
git checkout 006-subagent-tools
```

### 2. 了解现有架构

关键文件（重构前）：
- `backend/apps/graph/agent.py` — Agent 工厂，`create_chat_agent` 入口
- `backend/apps/graph/tools/` — 现有工具定义（search, memory, python_repl）
- `backend/apps/graph/services/agent_service.py` — Agent 执行服务（流式输出 + 监控）
- `backend/apps/context/templates/tool_usage.j2` — 工具使用 prompt 模板

### 3. 新增 SubAgent 模块

```bash
mkdir -p backend/apps/graph/subagents
```

创建文件：
- `__init__.py` — 注册表 + `get_subagent_tools()` 函数
- `base.py` — `run_subagent()` 工厂函数（创建 react agent + 超时）
- `search_agent.py` — 搜索 SubAgent
- `memory_agent.py` — 记忆 SubAgent
- `code_agent.py` — 代码执行 SubAgent

### 4. 启动后端验证

```bash
cd /home/dantsinghua/work/linchat/backend
uvicorn core.asgi:application --host 0.0.0.0 --port 8002 --reload
```

### 5. 功能测试

通过聊天界面验证（行为应与重构前完全一致）：

1. 普通对话："你好" → 正常回复
2. 搜索任务："搜索今天的新闻" → 返回搜索结果 + 引用来源
3. 代码执行："用 Python 计算 1+1" → 返回 2
4. 记忆操作："记住我喜欢蓝色" → 确认保存
5. 复合任务："搜索美元汇率，然后用 Python 计算 1 万美元等于多少人民币" → 协作完成

### 6. 运行测试

```bash
cd /home/dantsinghua/work/linchat/backend
pytest tests/apps/graph/test_subagents.py -v
```

---

## 新增 SubAgent 指南（扩展时参考）

新增一个 SubAgent 只需 2 步（修改不超过 2 个文件）：

**Step 1**: 在 `backend/apps/graph/subagents/` 下创建定义文件：

```python
# xxx_agent.py
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool

from apps.graph.subagents.base import run_subagent

PROMPT = "你是 XXX 助手。根据任务描述..."
TOOLS = [...]  # 内部工具列表

@tool
async def xxx_subagent(task: str, config: RunnableConfig) -> str:
    """工具描述（主 agent LLM 根据此描述决定何时调用）"""
    return await run_subagent(task, config, TOOLS, PROMPT)
```

**Step 2**: 在 `__init__.py` 的 `get_subagent_tools()` 中注册：

```python
if <条件检查>:
    from .xxx_agent import xxx_subagent
    tools.append(xxx_subagent)
```

---

## 禁用 SubAgent

如需禁用某个 SubAgent（如搜索），移除对应的配置条件即可。例如移除 `BRAVE_SEARCH_API_KEY` 环境变量后，搜索 SubAgent 将自动从注册表中移除，不影响其他功能。
