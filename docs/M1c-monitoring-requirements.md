# M1c: 动态监控 - 需求规划文档

## 1. 概述

### 1.1 背景
linchat 的上下文与记忆管理（M1b）需要配套的监控系统，实现 Token 实时计数与分布追踪、上下文使用率告警、Embedding 状态健康检查等能力。没有监控，上下文管理就是"盲人摸象"——不知道 Token 花在了哪里，也无法及时发现异常。

### 1.2 目标
- Token 实时计数：精确跟踪每次请求中各部分的 Token 占用
- Token 分布追踪：了解 system_prompt / 消息历史 / 记忆 / 用户输入各占多少
- 上下文使用率告警：接近或超出窗口上限时主动告警
- Embedding 健康检查：定时扫描异常状态记录并自动重试
- 前端状态展示：让用户/管理员能看到当前对话的上下文使用情况

### 1.3 前置依赖
- M1a 完成：model 表可用，能读取模型的 max_context_window

---

## 2. 功能需求

### 2.1 Token 实时计数（Token Counting）

**核心能力：** 对每次 LLM 请求，精确计算各组成部分的 Token 数量。

**Token 组成结构：**
```
总 Token = system_prompt + history_messages + retrieved_memories + user_input + buffer
           ─────────────────────────────────────────────────────────────────────
                     effective_context_window = model.max_context_window * 0.9
```

**各部分说明：**

| 部分 | 说明 | 预期占比 |
|------|------|---------|
| `system_prompt` | 系统提示词，一般固定 | 5-15% |
| `history_messages` | 历史对话消息 | 40-70% |
| `retrieved_memories` | 从记忆库召回的相关记忆 | 5-20% |
| `user_input` | 当前用户输入 | 1-10% |
| `buffer` | 预留给模型输出的空间 | 10%（由 M1b 的 90% 规则保证） |

**计数方法：**
```python
class TokenCounter:
    def __init__(self, model_name: str):
        """根据模型名选择对应的 tokenizer（tiktoken / 模型自带）"""
    
    def count(self, text: str) -> int:
        """计算单段文本的 token 数"""
    
    def count_messages(self, messages: List[Message]) -> int:
        """计算消息列表的 token 数（含角色标记等开销）"""
    
    def count_breakdown(self, state: AgentState) -> TokenBreakdown:
        """计算各部分 token 明细"""
```

**TokenBreakdown 数据结构：**
```python
@dataclass
class TokenBreakdown:
    system_prompt: int        # system prompt token 数
    history_messages: int     # 历史消息 token 数
    retrieved_memories: int   # 召回记忆 token 数
    user_input: int           # 当前用户输入 token 数
    total: int                # 以上之和
    max_allowed: int          # 有效上下文窗口 = model.max_context_window * 0.9
    usage_percent: float      # total / max_allowed * 100
    timestamp: float          # 计算时间戳
```

**计数精度要求：**
- 与实际 LLM 消耗的 token 数误差 < 5%
- 使用模型对应的 tokenizer（不同模型的 token 计算方式不同）

---

### 2.2 上下文使用率告警（Context Usage Alerting）

**告警级别：**

| 级别 | 触发条件 | 行为 |
|------|---------|------|
| 🟢 **正常** | usage_percent < 70% | 无动作 |
| 🟡 **注意** | 70% ≤ usage_percent < 85% | 日志记录 `WARNING`，前端展示黄色指示 |
| 🔴 **警告** | 85% ≤ usage_percent < 95% | 日志记录 `ERROR`，前端展示红色指示，建议用户开始新对话 |
| ⚠️ **临界** | usage_percent ≥ 95% | 触发自动裁剪/压缩流程，通知前端 |

**告警阈值（可配置）：**
```python
CONTEXT_ALERT_THRESHOLDS = {
    "info": 0.70,       # 70% 开始关注
    "warning": 0.85,    # 85% 发出警告
    "critical": 0.95,   # 95% 触发压缩
}
```

**额外告警规则：**
- 模型的 `max_context_window` < 32,000 tokens 时，在日志中记录 `WARNING`（提醒窗口较小）
- 单条用户输入超过有效窗口的 30% 时，发出 `WARNING`（异常长输入）

---

### 2.3 Embedding 健康检查（Embedding Health Check）

**定时任务：** 每小时执行一次

**检查逻辑：**
```python
async def embedding_health_check():
    """
    1. 扫描 embedding_status = 'failed' 的记录 → 自动重试
    2. 扫描 embedding_status = 'pending' 且 created_at > 1 小时前的记录 → 标记超时，重试
    3. 扫描 embedding_status = 'processing' 且超过 10 分钟的记录 → 标记超时，重试
    4. 汇总统计并记录日志
    """
```

**状态流转监控：**
```
pending → processing → done    （正常流程）
pending → processing → failed  （生成失败）
failed  → pending → ...        （重试）
pending（超时）→ failed → ...   （超时后重试）
```

**监控指标：**

| 指标 | 说明 | 告警条件 |
|------|------|---------|
| `embedding_pending_count` | 等待生成的记忆数 | > 50 条告警 |
| `embedding_failed_count` | 生成失败的记忆数 | > 10 条告警 |
| `embedding_avg_latency` | 平均生成耗时 | > 5 秒告警 |
| `embedding_retry_count` | 重试次数 | 单条 > 3 次放弃并记录 |

---

### 2.4 前端状态展示（Frontend Status Display）

**对话界面 - 上下文状态条：**

在对话界面底部或侧边展示当前对话的上下文使用情况：

```
┌─────────────────────────────────────────────┐
│ 📊 上下文使用: 45,200 / 57,600 tokens (78%) │
│ ██████████████████░░░░░  🟡                  │
│                                             │
│ 系统提示: 2,100 (4%)                         │
│ 历史消息: 35,800 (62%)                       │
│ 记忆召回: 4,300 (7%)                         │
│ 当前输入: 3,000 (5%)                         │
└─────────────────────────────────────────────┘
```

**API 响应附带监控数据：**

每次 LLM 响应中附带上下文状态信息：

```json
{
    "response": "...",
    "context_status": {
        "total_tokens": 45200,
        "max_tokens": 57600,
        "usage_percent": 78.5,
        "alert_level": "warning",
        "breakdown": {
            "system_prompt": 2100,
            "history_messages": 35800,
            "retrieved_memories": 4300,
            "user_input": 3000
        }
    }
}
```

**管理后台 - 监控面板（可选，后续迭代）：**
- 各用户的上下文使用趋势
- Embedding 健康状态汇总
- 记忆总结任务执行日志
- 系统级 Token 消耗统计

---

### 2.5 日志与审计（Logging & Audit）

**结构化日志格式：**
```python
# Token 使用日志（每次 LLM 调用后记录）
logger.info("context_usage", extra={
    "user_id": "user_123",
    "session_id": "session_456",
    "model": "gpt-4o",
    "total_tokens": 45200,
    "max_tokens": 57600,
    "usage_percent": 78.5,
    "breakdown": { ... },
    "alert_level": "warning",
    "action_taken": None,  # 或 "pruned" / "compressed"
})

# 裁剪/压缩事件日志
logger.warning("context_action", extra={
    "user_id": "user_123",
    "session_id": "session_456",
    "action": "compress",
    "messages_compressed": 12,
    "tokens_before": 58000,
    "tokens_after": 42000,
    "tokens_saved": 16000,
})

# Embedding 健康检查日志
logger.info("embedding_health", extra={
    "total_memories": 1500,
    "pending": 3,
    "failed": 1,
    "retried": 2,
    "avg_latency_ms": 1200,
})
```

---

## 3. 技术架构

### 3.1 LangGraph 状态扩展

监控系统维护独立的状态数据结构：

```python
class MonitoringState(TypedDict):
    context_tokens: int               # 当前总 token 数
    max_context_tokens: int           # model.max_context_window * 0.9
    token_breakdown: TokenBreakdown   # 各部分 token 明细
    alert_level: str                  # 'normal' | 'info' | 'warning' | 'critical'
    context_action: str | None        # 本轮是否触发了裁剪/压缩: 'pruned' | 'compressed' | None
    session_id: str
    user_id: str
```

**数据来源：**
- `max_context_tokens`：从 `model` 表读取 `max_context_window * 0.9`
- `context_tokens`：通过 TokenCounter 实时计算
- `token_breakdown`：通过 TokenCounter 分部计算
- `session_id` / `user_id`：从请求上下文获取

### 3.2 监控节点在 LangGraph 中的位置

```
[Input] 
    → [Memory Retrieval]
    → [Context Management]       ← 裁剪/压缩
    → [Context Monitoring] ★     ← Token 计数 + 告警判断 + 状态记录
    → [LLM Call]
    → [Response Generation]
    → [Post-call Monitoring] ★   ← 记录实际消耗 + 更新监控状态
    → [Memory Storage]
    → [Output]                   ← 响应中附带 context_status
```

### 3.3 监控服务接口

```python
class MonitoringService:
    def __init__(self, token_counter: TokenCounter):
        self.counter = token_counter
    
    def count_tokens(self, text: str) -> int:
        """计算单段文本 token 数"""
    
    def count_messages(self, messages: List[Message]) -> int:
        """计算消息列表 token 数"""
    
    def get_breakdown(self, state: AgentState) -> TokenBreakdown:
        """获取各部分 token 明细"""
    
    def evaluate_alert_level(self, breakdown: TokenBreakdown) -> str:
        """根据使用率判断告警级别"""
    
    def format_context_status(self, breakdown: TokenBreakdown) -> dict:
        """格式化为 API 响应附带的 context_status"""

class EmbeddingMonitor:
    async def health_check(self) -> EmbeddingHealthReport:
        """执行 embedding 健康检查，返回报告"""
    
    async def retry_failed(self, max_retries: int = 3) -> int:
        """重试失败的 embedding 记录，返回重试数量"""
    
    async def get_stats(self) -> dict:
        """获取 embedding 统计信息"""
```

---

## 4. 验收标准

### 4.1 Token 计数
- [ ] Token 计数与实际 LLM 消耗误差 < 5%
- [ ] 支持按 system_prompt / history / memory / user_input 分别计数
- [ ] 不同模型使用对应的 tokenizer

### 4.2 告警系统
- [ ] 四级告警（正常/注意/警告/临界）正确触发
- [ ] 临界告警时自动触发裁剪/压缩流程
- [ ] 告警阈值可通过配置修改
- [ ] 模型窗口 < 32,000 tokens 时记录警告日志

### 4.3 Embedding 健康检查
- [ ] 定时任务每小时执行
- [ ] 自动重试 failed/超时 pending 的记录
- [ ] 单条重试超过 3 次后放弃并记录
- [ ] 健康检查结果写入结构化日志

### 4.4 前端展示
- [ ] API 响应附带 context_status 字段
- [ ] 前端能展示 token 使用进度条和分布
- [ ] 告警级别有对应的视觉提示（颜色）

### 4.5 日志
- [ ] 每次 LLM 调用后记录 token 使用日志
- [ ] 裁剪/压缩事件记录日志
- [ ] Embedding 健康检查记录日志
- [ ] 日志格式为结构化 JSON

---

## 5. 依赖与风险

### 5.1 依赖
- M1a 完成（model 表可用）
- tiktoken 或对应模型的 tokenizer 库
- `user_memory` 表存在（Embedding 健康检查需要读取 `embedding_status` 字段）

### 5.2 风险

| 风险 | 影响 | 缓解措施 |
|------|------|----------|
| Token 计数不准 | 裁剪/压缩时机不对 | 使用模型对应 tokenizer + 预留 buffer |
| 监控开销影响性能 | 响应延迟增加 | Token 计数做缓存，不重复计算 |
| Embedding 重试风暴 | 服务压力大 | 指数退避重试 + 最大重试次数限制 |
| 前端状态展示延迟 | 用户看到旧数据 | 每次响应实时计算，不依赖缓存 |

---

## 6. 排期建议

| 阶段 | 内容 | 预估时间 |
|------|------|----------|
| Phase 1 | TokenCounter + TokenBreakdown 实现 | 1-2 天 |
| Phase 2 | 告警系统（四级告警 + 配置化阈值） | 1 天 |
| Phase 3 | Embedding 健康检查定时任务 | 1 天 |
| Phase 4 | API 响应附带 context_status | 0.5 天 |
| Phase 5 | 前端上下文状态条展示 | 1-2 天 |
| Phase 6 | 结构化日志 + 测试 | 1 天 |

**总计：约 5-7 天**

---

*文档版本：v1.0*
*创建日期：2026-01-29*
*作者：小鱼*
