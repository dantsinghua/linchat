# Implementation Plan: Jarvis 环境语音 — 多轮话语聚合 + 智能响应决策

**Branch**: `014-jarvis-ambient-voice` | **Date**: 2026-03-07 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/014-jarvis-ambient-voice/spec.md`

## Summary

在现有语音管道（010 + 013）之上，新增独立的 `ambient` 模式，面向 ESP 设备麦克风输入。核心增量：

1. **话语聚合层**（UtteranceAggregator）：在 VoiceConsumer 事件处理中插入聚合缓冲区，累积多段 ASR 转录直到静默超时（默认 3 秒），拼接后传入决策引擎
2. **增强响应决策引擎**：在现有 ResponseDecisionService 的 7 级规则链中第 4 级插入 LLM 意图分类（httpx 直连 DeepSeek API，JSON 模式，1s 超时），低置信度默认 RECORD_ONLY
3. **跨设备 TTS 路由**：ESP 设备仅上传音频，TTS 回复通过 Django Channels 分组广播到同一 user_id 的其他活跃 WebSocket 连接（手机浏览器等）
4. **环境监听会话管理**：ASR 连接长期存活（禁用空闲超时断开），会话 TTL 自动续期

现有 voice_chat 和 continuous_listen 模式行为完全不变。

## Technical Context

**Language/Version**: Python 3.11+ (后端) + TypeScript 5.0+ (前端 — 仅 ambient 模式类型定义)
**Primary Dependencies**: Django 4.2+ / channels / websockets 12.0+ / httpx / asyncio / LangGraph
**Storage**: PostgreSQL (Message 持久化) / Redis (会话状态 + Channels 分组)
**Testing**: pytest + pytest-django + pytest-asyncio
**Target Platform**: Linux server (ASGI/uvicorn)
**Project Type**: Web application (backend-heavy, minimal frontend changes)
**Performance Goals**: 聚合超时后 1s 内触发决策；LLM 分类 < 1s；ASR 连接持续 ≥ 30 分钟
**Constraints**: 单用户家庭系统；user_id 粒度隔离；ESP 设备仅输入不输出
**Scale/Scope**: 单用户，1 个 ESP 设备 + 1 个浏览器并发连接

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| 条款 | 要求 | 合规状态 | 说明 |
|------|------|----------|------|
| 1.1 关注点分离 | 分层架构 | ✅ 合规 | UtteranceAggregator 和 TTSRouter 作为 services 层新组件；VoiceConsumer 仅负责事件分发 |
| 1.2 接口设计 | WebSocket 协议 | ✅ 合规 | 复用现有 VoiceConsumer WS 协议，新增 `ambient` 模式枚举值 |
| 1.3 数据一致性 | PostgreSQL 为主 | ✅ 合规 | 聚合缓冲区为内存/Redis 临时数据；RECORD_ONLY 消息写入 PostgreSQL |
| 2.1 Python 规范 | PEP 8 + Black + 类型注解 | ✅ 合规 | 所有新文件遵循 |
| 3.1 测试覆盖 | 服务层 95% | ✅ 合规 | UtteranceAggregator + TTSRouter + 增强 ResponseDecisionService 均需 95%+ 覆盖 |
| 4.1 认证 | 设备 Token 认证 | ✅ 合规 | ESP 设备使用 RegisteredDevice 长效 Token（宪法 4.1 豁免条款） |
| 4.1 隔离粒度 | user_id 粒度 | ✅ 合规 | 所有缓冲区/决策/路由按 user_id 隔离 |
| 4.4 术语 | 单用户单会话 | ✅ 合规 | 无 conversation_id / session_id 引入 |
| 5.1 性能 | WebSocket < 500ms | ✅ 合规 | ESP WS 连接复用现有协议 |
| 8.2 ASGI | uvicorn 必须 | ✅ 合规 | 所有异步逻辑在 ASGI 事件循环中 |
| 9.2 单用户 | 禁止多用户并发控制 | ✅ 合规 | 无并发冲突机制 |

**无违规，GATE PASSED。**

## Project Structure

### Documentation (this feature)

```text
specs/014-jarvis-ambient-voice/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/           # Phase 1 output
│   └── websocket-protocol.md
└── tasks.md             # Phase 2 output (/speckit.tasks)
```

### Source Code (repository root)

```text
backend/
├── apps/voice/
│   ├── consumers.py                          # 修改: 新增 ambient 模式处理
│   ├── consumer_session.py                   # 修改: _normalize_mode 支持 ambient
│   ├── consumer_events.py                    # 修改: ambient 模式下转录事件走聚合器
│   ├── consumer_inference.py                 # 修改: ambient 模式禁用空闲超时
│   ├── services/
│   │   ├── utterance_aggregator.py           # 新增: 话语聚合缓冲区
│   │   ├── tts_router.py                     # 新增: 跨设备 TTS 路由
│   │   ├── response_decision_service.py      # 修改: 插入 LLM 分类第 4 级
│   │   ├── voice_pipeline.py                 # 修改: ambient 模式分支 + TTSRouter 回调
│   │   └── voice_session_service.py          # 修改: ambient 会话 TTL 续期
│   └── models.py                             # 不变（复用 RegisteredDevice）
├── core/
│   └── settings.py                           # 修改: 新增 VOICE_AMBIENT_* 配置
└── tests/voice/
    ├── test_utterance_aggregator.py           # 新增
    ├── test_tts_router.py                     # 新增
    ├── test_response_decision_llm.py          # 新增
    └── test_voice_pipeline.py                 # 修改: 新增 ambient 模式测试
```

**Structure Decision**: 后端为主的变更，新增 3 个 service 文件 + 3 个测试文件，修改 7 个现有文件。前端仅需在 `voice.ts` 类型定义中新增 `ambient` 模式枚举值。

## Architecture Design

### 核心数据流

```
ESP 设备 (PCM 音频)
    ↓ WebSocket (设备 Token 认证)
VoiceConsumer (mode=ambient)
    ↓ 转发 PCM 帧
Gateway ASR WebSocket (内置 VAD + auto_commit)
    ↓ transcription.completed 事件
consumer_events.py
    ↓ ambient 模式分支
UtteranceAggregator.add(text)
    ↓ 等待聚合超时 (默认 3 秒静默)
UtteranceAggregator → 聚合完成回调
    ↓ 聚合文本
ResponseDecisionService.decide(text, mode="ambient")
    ├── STOP → VoicePipeline.cancel()
    ├── RECORD_ONLY → _record_only() 保存消息
    └── RESPOND → VoicePipeline.run_pipeline()
                    ↓ Agent Pipeline (完整 LangGraph)
                    ↓ TTS 回复
                TTSRouter.route(user_id, audio_frames)
                    ↓ Django Channels group_send
                用户的浏览器 WS 连接 (播放 TTS)
```

### 组件设计

#### 1. UtteranceAggregator（话语聚合器）

**职责**：在 ambient 模式下缓冲多段 ASR 转录文本，检测静默超时后触发聚合

**状态机**：
```
IDLE → (收到转录) → COLLECTING → (静默超时) → AGGREGATED → (处理完成) → IDLE
                       ↑                              ↓
                       └── (新转录到达，重置计时器) ←──┘
```

**关键设计**：
- 每 user_id 一个实例，生命周期跟随 VoiceConsumer
- 内存中维护 `_utterances: list[str]` 缓冲区
- `asyncio.Task` 倒计时器，每次新转录到达时 cancel + 重建
- 聚合完成时调用回调 `on_aggregated(text: str)`
- 支持即时 flush（停止词触发）

**配置**：
- `VOICE_AMBIENT_AGGREGATE_TIMEOUT`: 聚合超时阈值（默认 3.0 秒）
- `VOICE_AMBIENT_MAX_BUFFER_SIZE`: 缓冲区最大话语数（默认 10）

#### 2. ResponseDecisionService 增强（LLM 分类）

**优先级链（ambient 模式）**：

| 优先级 | 规则 | 决策 | 说明 |
|--------|------|------|------|
| 1 | 紧急停止词 | STOP | 不变 |
| 2 | 精确唤醒词 | RESPOND | 不变 |
| 3 | 模糊唤醒词 | RESPOND | 不变 |
| 4 | **LLM 意图分类** | **视结果** | **新增**：httpx 调用 DeepSeek，JSON 模式，1s 超时 |
| 5 | 活跃对话状态 | RESPOND | 位置从第 4 降到第 5 |
| 6 | 问句特征 | RESPOND | 不变 |
| 7 | 默认 | RECORD_ONLY | 不变 |

**LLM 分类设计**：
- 仅在 `mode=ambient` 且 `VOICE_DECISION_USE_LLM=true` 时启用
- 使用 httpx 直连 DeepSeek API（非 LangChain），避免额外开销
- `response_format={"type": "json_object"}`，`temperature=0.1`，`max_tokens=100`
- 返回 `{decision, confidence, reason}`
- 置信度 < `VOICE_DECISION_LLM_THRESHOLD`（默认 0.7）时降级到后续规则
- 超时（1s）或异常时静默降级到规则引擎，不阻塞流程

#### 3. TTSRouter（跨设备 TTS 路由）

**职责**：ambient 模式下将 TTS 音频帧路由到同一 user_id 的其他活跃 WebSocket 连接

**实现机制**：
- 利用 Django Channels 已配置的 Redis 分组后端（settings.py CHANNEL_LAYERS）
- VoiceConsumer 在 `connect()` 时加入分组 `voice_tts_{user_id}`
- TTSRouter 通过 `channel_layer.group_send()` 广播音频帧
- ESP 设备连接（`_is_device_connection=True`）不接收 TTS 分组消息
- 浏览器连接接收并播放 TTS 音频

**关键设计**：
- VoiceConsumer 新增 `tts_audio_frame` handler 处理分组消息
- ESP 连接在 handler 中直接 return（不处理）
- TTSRouter 暴露 `async def send_binary(user_id, data)` 作为 on_audio 回调传给 TTSPipelineManager

#### 4. 环境监听会话管理

**变更**：
- `consumer_inference.py`：ambient 模式下禁用 `_idle_timeout_loop`
- `voice_session_service.py`：ambient 模式会话 TTL 延长到 3600s（1 小时），每次音频帧刷新
- ASR 连接保持 `ping_interval=30, ping_timeout=60` 维持心跳
- ASR 断连时自动重连（新增重连逻辑到 consumer_session.py）

**Redis 键扩展**：

| 键 | TTL | 用途 |
|----|-----|------|
| `voice:session:{uid}` | 3600s（ambient 模式） / 120s（其他模式） | 会话状态 |

## Complexity Tracking

无宪法违规，不需要追踪。
