# context/templates 指南

> Jinja2 Prompt 模板目录，由 `apps.context.loader.render()` 渲染。

## 模板文件

| 模板 | 用途 |
|------|------|
| `system_base.j2` | 基础 System Prompt 模板 |
| `behavior.j2` | 行为准则模块 |
| `reasoning.j2` | 推理能力模块 |
| `tool_usage.j2` | 工具使用指南模块 |
| `code_assist.j2` | 代码辅助模块 |
| `creative_writing.j2` | 创意写作模块 |
| `data_analysis.j2` | 数据分析模块 |
| `memory_context.j2` | 记忆上下文注入模板 |
| `memory_empty.j2` | 无记忆时的占位文本 |
| `tool_context.j2` | 工具上下文注入模板 |
| `conversation_history.j2` | 对话历史格式化模板 |
| `compaction_context.j2` | 压缩摘要上下文模板 |
| `compaction_task.j2` | 上下文压缩任务 Prompt |
| `daily_summary.j2` | 每日总结 Prompt |
| `monthly_summary.j2` | 月度总结 Prompt |
| `cronmem_extract.j2` | 定时记忆抽取 Prompt |

## 使用方式

```python
from apps.context.loader import render

text = render("behavior.j2")          # 无参数渲染
text = render("memory_context.j2", memories=[...])  # 带参数渲染
```
