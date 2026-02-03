# M1c: 动态监控 — 实现细化方案

> 基于 M1c-monitoring-requirements.md + 现有代码分析
> 创建日期：2026-02-03

---

## 0. 现有代码与消息流全景

### 0.1 现有基础设施

| 模块 | 文件 | 现状 | 可复用 |
|------|------|------|--------|
| TokenCounter | `apps/context/tokenizer.py` | `count_tokens()` + `count_messages_tokens()` | ✅ 直接用 |
| PromptBuilder | `apps/context/builder.py` | 分模块组装 system/memory/tool/history | ✅ 加 breakdown 返回 |
| Trimmer | `apps/context/trimmer.py` | L1-L3 优先级裁剪 | ✅ 加日志即可 |
| ContextService | `apps/chat/services/context_service.py` | 压缩流程 + token 限制检查 | ✅ 加事件发布 |
| AgentService | `apps/graph/services/agent_service.py` | preamble 构建 + SSE 流式 | ⚠️ 主要改动点 |
| EventService | `apps/common/event_service.py` | Redis PubSub 推 logout/heartbeat | ✅ 扩展事件类型 |

### 0.2 现有两条 SSE 流

```
Chat 流 (POST /api/v1/chat/)
  AgentService.execute() → yield StreamChunk → make_sse_response()
  data: {type, content, message_id, request_id}
  type: content | done | error | interrupted | context_compacting | context_compacted
  
  前端: chatService.ts → streamSSE() → switch(data.type) 分发 6 个回调
  
Event 流 (GET /api/v1/events/)
  EventService.subscribe_user_events() → Redis PubSub
  event: logout | heartbeat | message | connected
  
  前端: useAuth.tsx → 只处理 logout，其余忽略
```

### 0.3 工具调用 Token 消耗

LangGraph `create_react_agent` 消息流（单轮内）：

```
[preamble + history + HumanMessage]
    ↓ LLM 调用
AIMessage(tool_calls=[{name, args}])     ← 占 token（小）
ToolMessage(content="工具返回结果")        ← 占 token（可能大）
    ↓ LLM 再次调用（带上面所有消息）
AIMessage(content="最终回复")
```

- chat agent 不用 checkpointer，工具消息只在**单轮内**累积
- `_wrap_prompt()` 的 `trim_messages` 裁剪 history，**不裁剪当前轮工具消息**
- 当前没有统计工具调用/返回的 token 消耗

### 0.4 问题总结

1. **没有 token breakdown** — 只有总数，不知道哪部分占多少
2. **没有告警机制** — 超限才触发压缩，没有提前预警
3. **工具调用 token 盲区** — 不知道单轮工具调用累积了多少
4. **StreamChunk type 在膨胀** — 内容和状态混在一起
5. **Event 流利用率低** — 只用来推 logout

---

## 1. 设计原则：最小改动，最大收益

**不搞的事：**
- ❌ 不引入消息中间件/MQ
- ❌ 不重建 SSE 连接架构
- ❌ 不新建独立的 Event Bus 模块（现有 EventService 够用）
- ❌ 不拆分前端 SSE 连接（保持现有两条流）

**要搞的事：**
- ✅ TokenBreakdown 数据结构 + builder 返回
- ✅ 监控服务（ContextMonitor，一个文件）
- ✅ 扩展 EventService 发布监控事件（几行代码）
- ✅ AgentService 里加监控埋点
- ✅ 前端 Event 流增加 context_status 处理

**核心思路：Chat 流不动，监控事件走已有的 Event 流（Redis PubSub）。**

---

## 2. Phase 1: TokenBreakdown — 分部计数

### 2.1 新增数据结构

**文件：`apps/context/types.py`**

```python
@dataclass
class TokenBreakdown:
    """Token 分部计数"""
    
    # 静态部分（preamble 构建后确定）
    system_prompt: int = 0
    history_messages: int = 0
    retrieved_memories: int = 0
    compaction_summary: int = 0
    tool_definitions: int = 0
    user_input: int = 0
    
    # 动态部分（Agent 执行过程中累积）
    tool_calls: int = 0       # AIMessage.tool_calls 序列化
    tool_results: int = 0     # ToolMessage.content
    tool_call_count: int = 0
    
    @property
    def total(self) -> int:
        return (self.system_prompt + self.history_messages + 
                self.retrieved_memories + self.compaction_summary + 
                self.tool_definitions + self.user_input +
                self.tool_calls + self.tool_results)
    
    def usage_ratio(self, max_tokens: int) -> float:
        return self.total / max_tokens if max_tokens > 0 else 0.0
    
    def to_dict(self) -> dict:
        return {
            "system_prompt": self.system_prompt,
            "history": self.history_messages,
            "memories": self.retrieved_memories,
            "compaction": self.compaction_summary,
            "tool_defs": self.tool_definitions,
            "tool_calls": self.tool_calls,
            "tool_results": self.tool_results,
            "tool_count": self.tool_call_count,
            "user_input": self.user_input,
            "total": self.total,
        }
```

### 2.2 PromptBuilder 新增方法

**文件：`apps/context/builder.py`** — 新增 `build_preamble_with_breakdown()`

现有 `build_preamble()` 不动，新增带 breakdown 的版本：

```python
def build_preamble_with_breakdown(self, retrieved_memories=None, 
                                   compaction_summary=None, available_tools=None):
    from apps.context.tokenizer import count_tokens
    breakdown = TokenBreakdown()
    preamble = []
    
    sys_content = self.build_system_prompt()
    preamble.append(SystemMessage(content=sys_content))
    breakdown.system_prompt = count_tokens(sys_content)
    
    comp_text = self.build_compaction_block(compaction_summary)
    if comp_text:
        preamble.append(SystemMessage(content=comp_text))
        breakdown.compaction_summary = count_tokens(comp_text)
    
    mem_text = self.build_memory_block(retrieved_memories)
    if mem_text:
        preamble.append(SystemMessage(content=mem_text))
        breakdown.retrieved_memories = count_tokens(mem_text)
    
    tool_text = self.build_tool_context(available_tools)
    if tool_text:
        preamble.append(SystemMessage(content=tool_text))
        breakdown.tool_definitions = count_tokens(tool_text)
    
    return preamble, breakdown
```

### 2.3 _build_prompt_preamble() 改造

**文件：`apps/graph/services/agent_service.py`**

```python
# 返回值从 (preamble, preamble_tokens, effective_window)
# 改为     (preamble, breakdown, effective_window)

async def _build_prompt_preamble(user_id, user_message=""):
    # ... 现有逻辑不变 ...
    preamble, breakdown = builder.build_preamble_with_breakdown(
        retrieved_memories=retrieved_memories
    )
    breakdown.history_messages = sum(count_tokens(m.content or "") for m in history)
    breakdown.user_input = count_tokens(user_message)
    preamble = list(preamble) + history
    return preamble, breakdown, prompt_config.effective_window
```

**影响范围：** `execute()` 和 `resume()` 中调用处同步改 — `preamble_tokens` 改为 `breakdown.total`。

---

## 3. Phase 2: 监控服务 + 告警

### 3.1 新建 ContextMonitor

**新建文件：`apps/context/monitoring.py`**（单文件，不搞复杂结构）

```python
"""上下文监控 — 告警评估 + 结构化日志"""

import logging
from enum import Enum
from apps.context.types import TokenBreakdown

logger = logging.getLogger(__name__)

class AlertLevel(str, Enum):
    NORMAL = "normal"       # < 70%
    WARNING = "warning"     # 70-90%
    CRITICAL = "critical"   # >= 90%

# 简化为两级告警，够用就行
THRESHOLD_WARNING = 0.70
THRESHOLD_CRITICAL = 0.90

class ContextMonitor:
    
    @staticmethod
    def evaluate(breakdown: TokenBreakdown, max_tokens: int) -> AlertLevel:
        ratio = breakdown.usage_ratio(max_tokens)
        if ratio >= THRESHOLD_CRITICAL:
            return AlertLevel.CRITICAL
        if ratio >= THRESHOLD_WARNING:
            return AlertLevel.WARNING
        return AlertLevel.NORMAL
    
    @staticmethod
    def log_usage(user_id: int, breakdown: TokenBreakdown, 
                  max_tokens: int, alert: AlertLevel, **extra):
        """结构化日志"""
        data = {
            "user_id": user_id,
            "max_tokens": max_tokens,
            "usage_pct": round(breakdown.usage_ratio(max_tokens) * 100, 1),
            "alert": alert.value,
            "breakdown": breakdown.to_dict(),
            **extra,
        }
        if alert == AlertLevel.CRITICAL:
            logger.error("context_critical", extra=data)
        elif alert == AlertLevel.WARNING:
            logger.warning("context_warning", extra=data)
        else:
            logger.debug("context_usage", extra=data)
    
    @staticmethod
    def log_tool_result(user_id: int, tool_name: str, tokens: int):
        """工具结果 token 日志（超 2000 token 时 warning）"""
        if tokens > 2000:
            logger.warning("large_tool_result", extra={
                "user_id": user_id, "tool": tool_name, "tokens": tokens,
            })
    
    @staticmethod
    def format_status(breakdown: TokenBreakdown, max_tokens: int, alert: AlertLevel) -> dict:
        return {
            "total": breakdown.total,
            "max": max_tokens,
            "pct": round(breakdown.usage_ratio(max_tokens) * 100, 1),
            "alert": alert.value,
            "breakdown": breakdown.to_dict(),
        }
```

**为什么从四级简化为三级：** 原方案有 normal/info/warning/critical 四级，但 info 和 warning 对用户的行为引导没有本质区别（都是"注意一下"），合并为 warning 即可。

---

## 4. Phase 3: AgentService 埋点 + 事件发布

### 4.1 AgentService.execute() 改造

**改动原则：Chat 流（yield）不动，监控事件走 EventService → Redis PubSub → Event 流**

```python
# apps/graph/services/agent_service.py

async def execute(...):
    # ... 现有代码 ...
    
    preamble, breakdown, effective_window = await _build_prompt_preamble(user_id, user_message)
    
    # ★ 新增：评估 + 日志 + 发布 context_status
    alert = ContextMonitor.evaluate(breakdown, effective_window)
    ContextMonitor.log_usage(user_id, breakdown, effective_window, alert)
    await EventService.publish_event(user_id, "context_status", 
        ContextMonitor.format_status(breakdown, effective_window, alert),
        request_id=request_id)
    
    # ... 现有的 context 压缩检测逻辑不变 ...
    
    async with create_chat_agent(...) as agent:
        async for event in agent.astream_events(input_message, config=config, version="v2"):
            
            if event["event"] == "on_chat_model_stream":
                # ... 现有逻辑完全不变 ...
            
            elif event["event"] == "on_chat_model_end":
                output = event.get("data", {}).get("output")
                if output:
                    pt, ct = _extract_usage(output)
                    total_prompt_tokens += pt
                    total_completion_tokens += ct
                    
                    # ★ 新增：统计 tool_calls
                    if hasattr(output, "tool_calls") and output.tool_calls:
                        for tc in output.tool_calls:
                            tc_text = json.dumps(tc, ensure_ascii=False)
                            breakdown.tool_calls += count_tokens(tc_text)
                            breakdown.tool_call_count += 1
            
            # ★ 新增：捕获工具结果
            elif event["event"] == "on_tool_end":
                tool_output = str(event.get("data", {}).get("output", ""))
                result_tokens = count_tokens(tool_output)
                breakdown.tool_results += result_tokens
                tool_name = event.get("name", "unknown")
                
                ContextMonitor.log_tool_result(user_id, tool_name, result_tokens)
                
                # 重新评估 → 如果变严重了就推送更新
                new_alert = ContextMonitor.evaluate(breakdown, effective_window)
                if new_alert != alert:
                    alert = new_alert
                    await EventService.publish_event(user_id, "context_status",
                        ContextMonitor.format_status(breakdown, effective_window, alert),
                        request_id=request_id)
    
    # ... done 处理时附带 final breakdown ...
```

**关键点：**
- `yield StreamChunk` 部分**零改动**，前端 Chat 流解析不需要改
- 监控事件通过 `EventService.publish_event()` 走 Redis PubSub
- 只在告警级别**变化**时才推送更新，避免刷屏

### 4.2 EventService 扩展（极小改动）

**文件：`apps/common/event_service.py`** — 加一个通用发布方法

```python
class EventService:
    # ... 现有方法不动 ...
    
    @staticmethod
    async def publish_event(user_id: int, event_type: str, data: dict, 
                           request_id: str = None) -> bool:
        """通用事件发布"""
        try:
            client = await get_redis()
            channel = get_user_events_channel(user_id)
            payload = {"type": event_type, **data}
            if request_id:
                payload["request_id"] = request_id
            event = SSEEvent(
                event_type=EventType.MESSAGE,  # 复用 message 事件类型
                data=payload,
            )
            await client.publish(channel, event.to_sse_format())
            return True
        except Exception as e:
            logger.error("Failed to publish %s: %s", event_type, e)
            return False
```

**为什么不新建 EventBus 模块：** 现有 `EventService` + `SSEEvent` + Redis PubSub 已经是一个完整的事件总线。新增一个 `publish_event()` 方法就够了，不需要另起炉灶。

### 4.3 工具结果 token 限制

**文件：`apps/graph/tools/memory.py` + `apps/graph/tools/context.py`**

每个工具返回前加一行截断：

```python
MAX_TOOL_RESULT_TOKENS = 1500  # settings.py 可配置

def _cap_result(text: str, max_tokens: int = MAX_TOOL_RESULT_TOKENS) -> str:
    if count_tokens(text) <= max_tokens:
        return text
    # 按字符估算截断点（1 token ≈ 2-4 中文字符）
    chars = max_tokens * 2
    return text[:chars] + "\n[结果已截断]"
```

---

## 5. Phase 4: 前端接入

### 5.1 Event 流增加 context_status 处理

**文件：`frontend/src/hooks/useAuth.tsx`**

现有 `handleSSEEvent` 只处理 logout，扩展：

```typescript
// 改造前
const handleSSEEvent = useCallback((data: SSEEvent) => {
    if (data.type !== 'logout' || !data.reason) return;
    // ...
}, []);

// 改造后
const handleSSEEvent = useCallback((data: SSEEvent) => {
    switch (data.type) {
        case 'logout':
            // ... 现有逻辑不变 ...
            break;
        case 'context_status':
            // 通知聊天页面更新 context bar
            window.dispatchEvent(new CustomEvent('context_status', { detail: data }));
            break;
    }
}, []);
```

**为什么用 `CustomEvent` 而不是 state：** `useAuth` 在全局 layout 层，context_status 需要传给聊天页组件。用 CustomEvent 解耦最简单，不需要改 props 链或引入新 context。

### 5.2 ContextStatusBar 组件

**新建：`frontend/src/components/ContextStatusBar.tsx`**

```
┌──────────────────────────────────────┐
│ 📊 45,200 / 57,600 (78%) 🟡          │
│ ████████████████░░░░░                │
└──────────────────────────────────────┘
```

- `normal` → 不显示（不打扰用户）
- `warning` → 橙色条，显示在聊天输入框上方
- `critical` → 红色条 + "建议开始新对话"

**组件逻辑：**
```typescript
useEffect(() => {
    const handler = (e: CustomEvent) => setStatus(e.detail);
    window.addEventListener('context_status', handler);
    return () => window.removeEventListener('context_status', handler);
}, []);
```

### 5.3 ChatStreamEvent 类型不动

**`frontend/src/types/index.ts`** — 现有 `ChatStreamEvent.type` 不需要改！

因为 context_status 走 Event 流，不走 Chat 流。Chat 流的 6 个 type 保持原样。

---

## 6. Phase 5: Embedding 健康检查

**文件：`apps/memory/tasks.py`**

```python
@shared_task(name="memory.embedding_health_check")
def embedding_health_check():
    """每小时执行：扫描异常 embedding，重试 failed，标记 stuck"""
    from apps.memory.models import UserMemory
    from datetime import timedelta
    
    now = timezone.now()
    
    # failed + retry_count < 3 → 重置为 pending
    retried = UserMemory.objects.filter(
        embedding_status="failed", retry_count__lt=3
    ).update(embedding_status="pending", retry_count=models.F("retry_count") + 1)
    
    # pending 超 1 小时 或 processing 超 10 分钟 → 标记 failed
    stuck = UserMemory.objects.filter(
        models.Q(embedding_status="pending", updated_at__lt=now - timedelta(hours=1)) |
        models.Q(embedding_status="processing", updated_at__lt=now - timedelta(minutes=10))
    ).update(embedding_status="failed")
    
    total = UserMemory.objects.count()
    pending = UserMemory.objects.filter(embedding_status="pending").count()
    failed = UserMemory.objects.filter(embedding_status="failed").count()
    
    logger.info("embedding_health: total=%d pending=%d failed=%d retried=%d stuck=%d",
                total, pending, failed, retried, stuck)
    
    if failed > 10:
        logger.error("embedding_health_alert: %d failed", failed)
```

注册到 Celery Beat：`"schedule": crontab(minute=0)`

---

## 7. Phase 6: 结构化日志

**文件：`core/settings.py`** — LOGGING 配置

```python
LOGGING["loggers"]["apps.context.monitoring"] = {
    "handlers": ["console"],  # 先走 console，后续可加 file handler
    "level": "DEBUG",
}
```

不单独建日志文件，先和现有日志混在一起。monitoring 的日志通过 logger name 过滤即可。

---

## 8. 改动文件汇总

| Phase | 文件 | 操作 | 改动量 |
|-------|------|------|--------|
| 1 | `apps/context/types.py` | 修改 | +40 行（TokenBreakdown） |
| 1 | `apps/context/builder.py` | 修改 | +25 行（build_preamble_with_breakdown） |
| 1 | `apps/graph/services/agent_service.py` | 修改 | ~30 行（返回值改 + 调用处适配） |
| 2 | `apps/context/monitoring.py` | **新建** | ~60 行（ContextMonitor） |
| 3 | `apps/graph/services/agent_service.py` | 修改 | ~40 行（埋点 + on_tool_end + EventService.publish_event） |
| 3 | `apps/common/event_service.py` | 修改 | +15 行（publish_event 方法） |
| 3 | `apps/graph/tools/memory.py` | 修改 | +5 行（_cap_result） |
| 3 | `apps/graph/tools/context.py` | 修改 | +5 行（_cap_result） |
| 4 | `frontend/src/hooks/useAuth.tsx` | 修改 | +10 行（handleSSEEvent 扩展） |
| 4 | `frontend/src/components/ContextStatusBar.tsx` | **新建** | ~60 行 |
| 5 | `apps/memory/tasks.py` | 修改 | +25 行（embedding_health_check） |
| 6 | `core/settings.py` | 修改 | +5 行（日志配置 + 常量） |

**总改动：~320 行，新建 2 个文件**

---

## 9. 架构对比

### 改造前

```
AgentService → yield StreamChunk(6种type) → Chat SSE → 前端 switch 6 分支
EventService → Redis PubSub(logout/heartbeat) → Event SSE → 前端只处理 logout
```

### 改造后

```
AgentService → yield StreamChunk(6种type不变) → Chat SSE → 前端不动
           ↘ EventService.publish_event("context_status") ↘
EventService → Redis PubSub(logout/heartbeat/context_status) → Event SSE
                                                                  → 前端 switch 加 1 分支
```

**Chat 流零改动。Event 流加一种事件。就这么简单。**

---

## 10. 监控触发时机图

```
用户发消息
    │
    ▼
_build_prompt_preamble()
    │ → breakdown（静态部分）
    │
    ▼
★ 埋点1: ContextMonitor.evaluate() → log → publish context_status
    │
    ├── if CRITICAL → 现有压缩逻辑（不变）
    │
    ▼
astream_events() 循环
    │
    ├── on_chat_model_stream → yield content（不变）
    │
    ├── on_chat_model_end → 统计 usage + tool_calls token
    │
    ├── on_tool_end → ★ 埋点2: breakdown.tool_results += N
    │                  → 如果 alert 级别变化 → publish context_status 更新
    │
    └── 生成完成 → yield done（不变）
                  → ★ 埋点3: log 最终 breakdown
```

---

## 11. 排期

| 阶段 | 内容 | 依赖 | 预估 |
|------|------|------|------|
| P1 | TokenBreakdown + builder + agent_service 适配 | 无 | 0.5 天 |
| P2 | monitoring.py + agent_service 埋点 + EventService 扩展 | P1 | 1 天 |
| P3 | 工具 token 限制（_cap_result） | 无（可并行） | 0.5 天 |
| P4 | 前端 useAuth 扩展 + ContextStatusBar | P2 | 1 天 |
| P5 | embedding_health_check | 无（可并行） | 0.5 天 |
| P6 | 日志配置 + 测试 | P1-P4 | 0.5 天 |

**总计：4 天**（比 v1.2 的 7 天少 3 天）

**可并行：P1+P3+P5 同时开工，P2 等 P1 完成，P4 等 P2 完成**

---

## 12. 与 v1.2 方案对比

| 维度 | v1.2（上版） | v1.3（本版） |
|------|-------------|-------------|
| 新文件 | 3 个（monitoring.py + event_bus.py + ContextStatusBar） | 2 个（monitoring.py + ContextStatusBar） |
| Event Bus | 新建独立模块，全套 EventType 枚举 + EventBus 类 | 复用 EventService，加一个 publish_event 方法 |
| Chat 流改动 | StreamChunk type 改枚举 + 精简为 4 种 | **零改动** |
| 前端 Chat 流 | 重构 switch-case | **不动** |
| 前端 Event 流 | addEventListener 多事件 | switch 加 1 分支 |
| 告警级别 | 4 级（normal/info/warning/critical） | 3 级（normal/warning/critical） |
| SSE 连接数 | 保持 2 条 | 保持 2 条 |
| 总改动量 | ~600+ 行 | ~320 行 |
| 工期 | 7 天 | 4 天 |

**v1.2 的问题：** EventBus 是对的方向，但在只需要推 context_status 一种新事件的阶段，建一整套 EventBus + EventType 枚举 + 前端 addEventListener 重构，属于过度设计。等未来事件类型真的多到 10+ 种再重构不迟。

---

*文档版本：v1.3 — 精简版，最小改动*
*创建日期：2026-02-03*
*作者：小鱼*
