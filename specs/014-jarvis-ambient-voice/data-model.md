# Data Model: 014-jarvis-ambient-voice

## 概述

本特性**不新增数据库模型**。所有新增实体为运行时内存/Redis 临时数据。复用现有 Message、RegisteredDevice、VoiceSettings 模型。

## 运行时实体

### UtteranceBuffer（话语缓冲区）

**存储位置**：VoiceConsumer 实例内存（UtteranceAggregator 成员）

| 属性 | 类型 | 说明 |
|------|------|------|
| utterances | list[str] | 缓冲区中的转录文本列表（按时间顺序） |
| timestamps | list[float] | 每段转录的到达时间戳 |
| last_activity | float | 最后一次收到转录的时间（time.monotonic） |
| timeout_task | asyncio.Task \| None | 当前活跃的超时倒计时任务 |

**生命周期**：
- 创建：VoiceConsumer 进入 ambient 模式时
- 销毁：VoiceConsumer 断开时（随 Consumer 实例 GC）

### AggregatedMessage（聚合消息）

**存储位置**：仅作为函数参数传递，不持久化

| 属性 | 类型 | 说明 |
|------|------|------|
| text | str | 聚合后的完整文本（utterances 用空格拼接） |
| utterance_count | int | 包含的话语段数 |
| first_ts | float | 第一段话语的时间戳 |
| last_ts | float | 最后一段话语的时间戳 |

### ResponseDecision（响应决策）

**存储位置**：仅作为函数返回值，不持久化

| 属性 | 类型 | 说明 |
|------|------|------|
| decision | DecisionResult | RESPOND / RECORD_ONLY / STOP |
| reason | str | 判定原因（如 "llm_intent_respond", "exact_wake_word"） |
| confidence | float | 置信度（0.0-1.0，仅 LLM 分类时有意义） |

## 复用的现有模型

### Message（消息）

RECORD_ONLY 决策时保存：
- `role` = "user"
- `is_voice` = True
- `content` = 聚合后的文本
- 无对应的 assistant 消息

### RegisteredDevice（注册设备）

ESP 设备认证：
- `api_token_encrypted`：SM4 加密的设备 Token
- `token_prefix`：快速查找前缀
- `last_active_at`：最后活跃时间（每次 WS 连接时更新）

## Redis 键扩展

| 键 | TTL | 数据类型 | 说明 |
|----|-----|----------|------|
| `voice:session:{uid}` | 3600s (ambient) / 120s (其他) | Hash | 会话状态，ambient 模式延长 TTL |
| `voice:active_conv:{uid}` | 30s | String | 活跃对话标记（ambient RESPOND 后设置） |

**注意**：聚合缓冲区不使用 Redis，完全在 Consumer 进程内存中管理。

## 配置参数扩展（settings.py）

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| VOICE_AMBIENT_AGGREGATE_TIMEOUT | float | 3.0 | 聚合静默超时（秒） |
| VOICE_AMBIENT_MAX_BUFFER_SIZE | int | 10 | 单次聚合最大话语段数 |
| VOICE_AMBIENT_SESSION_TTL | int | 3600 | ambient 模式会话 TTL（秒） |
| VOICE_AMBIENT_RECORD_ONLY_LIMIT | int | 20 | RECORD_ONLY 消息保留上限 |
| VOICE_DECISION_USE_LLM | bool | False | 是否启用 LLM 意图分类 |
| VOICE_DECISION_LLM_THRESHOLD | float | 0.7 | LLM 分类置信度阈值 |
| VOICE_DECISION_LLM_TIMEOUT | float | 1.0 | LLM 分类调用超时（秒） |
