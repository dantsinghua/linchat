# SubAgent 模块

## 职责

封装各类 SubAgent 定义和注册逻辑。每个 SubAgent 是一个 `@tool` 装饰的异步函数，
内部创建 `create_react_agent` 管理独立工具链，对主 Agent 而言是一个黑盒工具。

## 使用方式

通过 `get_subagent_tools()` 获取当前可用的 SubAgent 工具列表：

```python
from apps.graph.subagents import get_subagent_tools

tools = get_subagent_tools()  # 返回 SubAgent tool 函数列表
```

## 扩展指南

新增 SubAgent 仅需 2 步（修改不超过 2 个文件）：

1. 在本目录下创建定义文件（如 `xxx_agent.py`），包含 PROMPT 常量和 `@tool` 函数
2. 在 `__init__.py` 的 `get_subagent_tools()` 中注册

## 文件结构

- `__init__.py` — SubAgent 注册表，导出 `get_subagent_tools()`
- `base.py` — `run_subagent()` 工厂函数 + `get_common_tools()` 公共工具
- `search_agent.py` — 搜索 SubAgent
- `memory_agent.py` — 记忆 SubAgent
- `code_agent.py` — 代码执行 SubAgent
