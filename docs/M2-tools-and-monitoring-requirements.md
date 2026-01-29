# M2: 工具调用与实时监控 - 需求规划文档

## 1. 概述

### 1.1 背景
M1 完成了 Agent 核心数据层（模型配置、上下文管理、长期记忆）。M2 在此基础上构建工具调用能力和实时状态监控面板。

### 1.2 目标
- 构建可扩展的工具调用框架，实现 3 个核心工具
- 构建实时状态监控面板，可视化 Agent 运行状态

### 1.3 前置依赖
- M1 完成：模型配置表、上下文管理、记忆系统可用

---

## 2. 功能需求

### 2.1 工具调用框架（Tool Calling Framework）

**需求描述：**
构建可扩展的工具调用系统，**M2 阶段实现以下 3 个工具**：

#### 2.1.1 Python REPL

执行 Python 代码并返回结果。

**安全防护要求：**
- 沙箱执行（限制文件系统访问）
- 执行超时限制（默认 30 秒）
- 内存限制
- 禁止网络访问（或限制）
- 禁止危险操作（`os.system`、`subprocess`、`shutil.rmtree` 等）

```python
class PythonREPLTool:
    """安全的 Python REPL 工具"""
    timeout: int = 30  # 秒
    max_memory_mb: int = 256
    allowed_modules: List[str] = ["math", "json", "re", "datetime", ...]
    blocked_builtins: List[str] = ["exec", "eval", "compile", "open", "__import__", ...]
```

#### 2.1.2 Brave API 网页搜索

使用 Brave Search API 进行网页搜索。

```python
class BraveSearchTool:
    """Brave Search API 网页搜索"""
    api_key: str  # Brave API Key
    max_results: int = 5
    
    async def search(self, query: str) -> List[SearchResult]:
        """搜索并返回标题、URL、摘要"""
```

#### 2.1.3 Home Assistant 控制

接入 Home Assistant，实现智能家居控制。

```python
class HomeAssistantTool:
    """Home Assistant 智能家居控制"""
    url: str       # HA API URL
    token: str     # Long-Lived Access Token
    
    async def get_devices(self) -> List[Device]: ...
    async def get_state(self, entity_id: str) -> DeviceState: ...
    async def turn_on(self, entity_id: str) -> bool: ...
    async def turn_off(self, entity_id: str) -> bool: ...
    async def set_brightness(self, entity_id: str, brightness: int) -> bool: ...
    async def trigger_scene(self, scene_id: str) -> bool: ...
```

**配置项：**
```yaml
home_assistant:
  url: "http://192.168.1.100:8123"
  token: "YOUR_LONG_LIVED_ACCESS_TOKEN"
```

**工具框架设计（可扩展）：**
```python
class ToolService:
    def register(self, tool: BaseTool) -> None: ...
    async def execute(self, tool_call: ToolCall) -> ToolResult: ...
    def get_schemas(self) -> List[dict]: ...  # OpenAI function calling 格式
```

---

### 2.2 实时状态监控面板

**需求描述：**
在 Chat 页面右侧增加侧边栏，实时展示大模型、记忆、上下文的运行状态。通过 WebSocket 推送数据，每 **500ms** 统计并刷新。

**整体风格：** 参考 Windows 任务管理器 / 资源管理器的看板式、监控式布局，与现有 UI 风格保持一致。

#### 2.2.1 硬盘（Embedding 处理）

**i. Embedding 速度折线图**
- 数据：embedding 模型 token 输入/输出数（每 500ms 累计）
- 图表：双折线图
  - 蓝色线：输入 token 数
  - 橘黄色线：输出 token 数
- 刷新：每 500ms

**ii. 当前记忆组成**
- 数据：记忆中不同类型内容（memory / image / file / audio / video）的 token 大小及占比
- 图表：横向堆叠柱状图
- 刷新：每 500ms，与折线图同步

#### 2.2.2 CPU（大模型处理）

**iii. 大模型处理能力**
- 数据：
  - 当前模型名称（model name）
  - 输入/输出 token 数（每 500ms 累计）
- 图表：双折线图
  - 蓝色线：输入 token 数
  - 橘黄色线：输出 token 数
- 刷新：每 500ms

#### 2.2.3 内存（上下文窗口）

**iv. 上下文窗口组成与趋势**
- 数据：
  - 总容量：**模型表中配置的 `max_context_window`**（注意：是 100% 原始值，不是 90%）
  - 组成部分：`system_prompt` / `消息历史` / `记忆` / `工具` / `user_input` 各自的 token 占用
- 图表 A：横向堆叠柱状图（各部分 token 占比）
- 图表 B：汇总值时间趋势折线图（总 token 使用量随时间变化）
- 刷新：每 500ms，图表 A 和 B 同步更新

#### 2.2.4 当前进程（工具调用）

**v. 工具调用实时列表**
- 数据：
  - 当前模型正在调用的工具名称
  - 对应任务名称
  - 花费的 token 数量
  - 输出结果的 token 数
- 展示：实时列表，每 500ms 显示累计值
- 排序：按输出结果 token 数**倒序**排列

#### 2.2.5 技术实现

**数据通道：** WebSocket 实时推送
```json
{
  "type": "status_update",
  "timestamp": 1706500000000,
  "embedding": {
    "input_tokens": 1234,
    "output_tokens": 567
  },
  "memory": {
    "composition": {
      "memory": 2048,
      "image": 0,
      "file": 512,
      "audio": 0,
      "video": 0
    }
  },
  "llm": {
    "model_name": "gpt-4o",
    "input_tokens": 5000,
    "output_tokens": 1200
  },
  "context": {
    "max_window": 128000,
    "composition": {
      "system_prompt": 2000,
      "message_history": 8000,
      "memory": 3000,
      "tools": 1500,
      "user_input": 500
    }
  },
  "tools": [
    {
      "name": "python_repl",
      "task": "计算数据统计",
      "input_tokens": 200,
      "output_tokens": 150
    }
  ]
}
```

**前端要求：**
- 所有监控图表**同步刷新、同步更新**
- 配色与现有 UI 保持一致
- 布局参考 Windows 任务管理器/资源管理器
- 右侧侧边栏可折叠

---

## 3. 接口定义

### 3.1 Tool Service Interface

```python
class ToolService:
    def register(self, tool: BaseTool) -> None:
        """注册工具"""
    
    async def execute(self, tool_call: ToolCall) -> ToolResult:
        """执行工具"""
    
    def get_schemas(self) -> List[dict]:
        """获取工具 schema 供 LLM 使用（OpenAI function calling 格式）"""
```

### 3.2 Status WebSocket Interface

```python
class StatusBroadcaster:
    async def broadcast(self, status: StatusUpdate):
        """通过 WebSocket 广播状态更新"""
    
    def collect_metrics(self, state: AgentState) -> StatusUpdate:
        """从 AgentState 收集监控指标"""
```

---

## 4. 验收标准

### 4.1 工具调用
- [ ] Python REPL 可用，安全防护到位
- [ ] Brave Search API 搜索可用
- [ ] Home Assistant 设备控制可用
- [ ] 工具执行不阻塞主响应流
- [ ] 工具框架可扩展，新增工具只需实现 BaseTool 接口

### 4.2 状态监控面板
- [ ] 右侧栏实时展示所有监控数据
- [ ] 所有图表 500ms 同步刷新
- [ ] 布局风格与现有 UI 一致，参考 Windows 任务管理器
- [ ] WebSocket 连接稳定，断线自动重连

### 4.3 可测试性
- [ ] 每个工具有独立的单元测试
- [ ] Home Assistant 有 mock 测试支持
- [ ] Python REPL 安全防护有测试覆盖

---

## 5. 依赖与风险

### 5.1 依赖
- M1 完成（模型配置、上下文管理、记忆系统）
- Home Assistant 实例可访问
- Brave Search API Key

### 5.2 风险

| 风险 | 影响 | 缓解措施 |
|------|------|----------|
| HA 连接不稳定 | 设备控制失败 | 重试机制 + 超时降级 |
| Python REPL 安全 | 系统风险 | 沙箱 + 超时 + 资源限制 |
| WebSocket 压力 | 前端卡顿 | 节流 + 按需推送 |

---

## 6. 排期建议

| 阶段 | 内容 | 预估时间 |
|------|------|----------|
| Phase 1 | 工具框架 + Python REPL | 2-3 天 |
| Phase 2 | Brave 搜索 + Home Assistant | 2-3 天 |
| Phase 3 | 实时状态监控面板（WebSocket + 前端） | 4-5 天 |
| Phase 4 | 集成测试与调优 | 1-2 天 |

**总计：约 2 周**

---

*文档版本：v1.0*
*创建日期：2026-01-29*
*作者：小鱼*
