# Implementation Plan: Ambient 模式说话人识别

**Branch**: `017-ambient-speaker-id` | **Date**: 2026-04-15 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/017-ambient-speaker-id/spec.md`

## Summary

在 Ambient 模式下激活已有的声纹识别基础设施，实现说话人自动识别、TTS 回声过滤和未知说话人管理。核心策略是**复用 Gateway 已有 Speaker Identify API + LinChat 已有 SpeakerProfile/speaker_service**，改动范围最小化。

## Technical Context

**Language/Version**: Python 3.12 (backend), TypeScript 5.0+ (frontend)
**Primary Dependencies**: Django 4.2+, DRF, Channels 4.0+, httpx (Gateway HTTP), Zustand (frontend state)
**Storage**: PostgreSQL (Message.speaker_id, SpeakerProfile), Redis (TTS 状态标记, 临时标签映射, PCM 音频缓存)
**Testing**: pytest + pytest-asyncio (backend), Jest (frontend)
**Target Platform**: Linux server (单节点生产环境)
**Project Type**: Web (backend + frontend)
**Performance Goals**: 说话人识别不增加 > 500ms 感知延迟 (SC-004)
**Constraints**: 异步非阻塞、功能开关可回退、零数据库迁移
**Scale/Scope**: 家庭 2-5 人，单并发处理

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| 条款 | 检查项 | 状态 | 说明 |
|------|--------|------|------|
| 1.1 分层架构 | 业务逻辑在服务层 | ✅ | 所有识别逻辑在 speaker_service / response_decision_service |
| 1.2 接口设计 | WebSocket 事件规范 | ✅ | `speaker.identified` 事件已在前端定义 |
| 1.3 数据一致性 | PostgreSQL 为主 | ✅ | Message.speaker_id 持久化到 PG，Redis 仅做缓存 |
| 1.4 简单设计 | 最简方案 | ✅ | 复用 Gateway 已有 API，零新模型/框架 |
| 2.1 Python 规范 | 类型注解+文档字符串 | ✅ | 遵循现有代码风格 |
| 2.2 TS 规范 | 严格模式+interface | ✅ | 前端类型已定义 |
| 3.1 测试覆盖 | 服务层 >= 95% | ⚠️ | 需新增 ~29 测试用例 |
| 4.1 数据隔离 | user_id 粒度 | ✅ | 识别后按 user_id 隔离上下文 |
| 4.3 LLM 异常 | 统一处理 | N/A | 无新增 LLM 调用（Gateway HTTP） |
| 4.4 术语 | user_id 隔离 | ✅ | 保持一致 |
| 5.1 性能 | 不阻塞主路径 | ✅ | 异步调用，不阻塞 ASR 流 |
| 9.2 并发模型 | 单并发+多档案 | ✅ | 识别多用户但逐一处理 utterance |

**Gate Result**: ✅ PASS — 无违规，3.1 测试覆盖通过新增测试用例保障。

## Project Structure

### Documentation (this feature)

```text
specs/017-ambient-speaker-id/
├── spec.md              # 特性规范（已完成）
├── plan.md              # 本文件
├── research.md          # Phase 0 调研输出
├── data-model.md        # Phase 1 数据模型
├── quickstart.md        # Phase 1 快速上手
├── contracts/           # Phase 1 WebSocket 事件契约
└── tasks.md             # Phase 2 任务清单（/speckit.tasks 生成）
```

### Source Code (repository root)

```text
backend/
├── apps/voice/
│   ├── services/
│   │   ├── speaker_service.py       # [修改] 新增 identify_from_pcm()
│   │   ├── response_decision_service.py  # [修改] 新增 Level 0 TTS 回声检测
│   │   ├── tts_router.py            # [修改] TTS 播放状态 Redis 标记
│   │   ├── voice_persist_service.py  # [修改] 持久化 speaker_id
│   │   ├── utterance_aggregator.py  # [无改动] 已有 per-speaker 能力
│   │   └── voice_pipeline.py        # [微调] 使用识别到的 user_id
│   ├── consumer_events.py           # [修改] ambient 路径增加 speaker identify
│   ├── consumer_session.py          # [微调] 启用 _speaker_aggregators
│   └── models.py                    # [无改动] SpeakerProfile 已就绪
├── core/
│   └── settings.py                  # [修改] 新增功能开关
└── tests/voice/
    ├── test_speaker_identification.py    # [新建]
    ├── test_tts_echo_detection.py        # [新建]
    ├── test_unknown_speaker_labeling.py  # [新建]
    └── test_response_decision_service.py # [扩展]

frontend/src/
├── components/voice/
│   └── VoiceMessageBubble.tsx       # [修改] 显示说话人标识
├── hooks/
│   └── useVoiceWebSocket.ts         # [无改动] handler 已映射
├── stores/
│   └── voiceStore.ts                # [修改] speaker mapping 管理
└── types/
    └── voice.ts                     # [无改动] SpeakerProfile 类型已定义
```

## Existing Code Analysis

### 已就绪（零改动可用）

| 组件 | 文件 | 状态 | 说明 |
|------|------|------|------|
| SpeakerProfile 模型 | `voice/models.py:5-20` | 生产就绪 | OneToOne→SysUser, gateway_speaker_id, quality_score |
| Message.speaker_id 字段 | `chat/models.py:23` | 存在但未填充 | CharField(100, nullable) |
| identify_speaker() | `speaker_service.py:66-72` | 工作中 | 按 gateway_speaker_id 查 SpeakerProfile |
| Gateway 注册 API 模式 | `speaker_service.py:25-54` | 生产就绪 | httpx + Bearer auth 模式可复用 |
| PCM 音频缓存 | `consumer_session.py:217` | 生产就绪 | Redis `voice:audio_chunks:{uid}:{seg}` |
| speaker_identified 参数 | `response_decision_service.py:30` | 已激活 | decide() 签名已包含 |
| _speaker_aggregators 字典 | `consumer_session.py:76` | 已初始化 | `{}` 但未使用 |
| 前端 currentSpeakerId | `voiceStore.ts:29` | 已定义 | state + setter，值为 null |
| 前端 speaker.identified 事件 | `useVoiceWebSocket.ts:97` | 已映射 | EVENT_HANDLER_MAP 已包含 |
| 前端 SpeakerProfile 类型 | `voice.ts:22-28` | 已定义 | interface 完整 |
| VOICE_SPEAKER_THRESHOLD | `settings.py:433` | 已配置 | 默认 0.5 |

### 需要修改

| 组件 | 文件 | 改动类型 | 复杂度 |
|------|------|---------|--------|
| speaker_service | `speaker_service.py` | 新增 `identify_from_pcm()` 方法 | 低 |
| consumer_events | `consumer_events.py:60-81` | `_handle_ambient_transcription` 增加识别调用 | 中 |
| consumer_session | `consumer_session.py` | 启用 `_speaker_aggregators`，管理临时标签 | 中 |
| response_decision | `response_decision_service.py` | 新增 Level 0 TTS 回声检测 | 低 |
| tts_router | `tts_router.py` | TTS 播放 Redis 状态标记 | 低 |
| voice_persist | `voice_persist_service.py:98-112` | `record_only_ambient` 存储 speaker_id | 低 |
| voice_pipeline | `voice_pipeline.py:41-43` | 使用识别到的 user_id | 低 |
| settings | `settings.py` | 新增功能开关 | 低 |
| VoiceMessageBubble | `VoiceMessageBubble.tsx` | 显示说话人 UI | 中 |
| voiceStore | `voiceStore.ts` | speaker mapping 持久化 | 低 |

## Implementation Phases

### Phase 1: Core Speaker Identification (P1 — 核心价值)

**目标**: ambient 模式下每段语音转录完成后自动识别说话人

**改动文件**:

1. **`speaker_service.py`** — 新增 `identify_from_pcm(pcm_data: bytes) -> dict`
   - 从 Redis 取 PCM chunks → 拼接 → base64 编码
   - POST Gateway `/v1/voice/speakers/identify` (复用 `build_gateway_headers()` 模式)
   - 返回 `{identified: bool, speaker_id: str|None, confidence: float, embedding_hash: str|None}`
   - 异常降级：Gateway 不可用时返回 `{identified: False}`

2. **`consumer_events.py`** — 修改 `_handle_ambient_transcription()`
   - 在 `transcription.completed` 后，取 PCM chunks 调用 `identify_from_pcm()`
   - 识别成功：查 SpeakerProfile → 获取 user_id → 传递给下游
   - 识别失败：分配/复用临时标签
   - 发送 `speaker.identified` WebSocket 事件
   - **功能开关**: `VOICE_SPEAKER_IDENTIFICATION_ENABLED` 为 False 时跳过

3. **`consumer_session.py`** — 启用 per-speaker 聚合
   - 激活 `_speaker_aggregators` 用于按 speaker_user_id 分组

4. **`voice_persist_service.py`** — `record_only_ambient()` 增加 `speaker_id` 参数
   - 创建 Message 时写入 `speaker_id` 字段

5. **`voice_pipeline.py`** — `run_pipeline()` 使用识别到的 `user_id` 而非连接 user_id

6. **`settings.py`** — 新增:
   - `VOICE_SPEAKER_IDENTIFICATION_ENABLED = env.bool(default=False)`
   - 复用已有 `VOICE_SPEAKER_THRESHOLD = 0.5`

7. **测试**: `test_speaker_identification.py` (~12 用例)
   - identify_from_pcm 正确调用 Gateway API
   - 识别成功映射到 user_id
   - 识别失败返回降级结果
   - 功能开关关闭时跳过识别
   - 音频 < 0.5s 跳过识别

### Phase 2: TTS Echo Filtering (P1 — 防止循环)

**目标**: 过滤 TTS 回声，防止无限循环消耗 token

**改动文件**:

1. **`response_decision_service.py`** — 新增 Level 0 (最高优先级)
   - `_is_tts_echo(text, user_id) -> bool`
   - 策略 1: Redis `voice:tts_playing:{user_id}` 时间窗口检测
   - 策略 2: Redis `voice:tts_history:{user_id}` 文本相似度比对 (> 0.7)
   - 返回 `(DecisionResult.DISCARD, "tts_echo_detected")`

2. **`tts_router.py`** — TTS 播放状态标记
   - TTS 开始: `SETEX voice:tts_playing:{user_id} 30 "1"`
   - TTS 结束: `DEL voice:tts_playing:{user_id}`
   - 记录文本: `LPUSH voice:tts_history:{user_id} {tts_text}` + `LTRIM 0 9` + `EXPIRE 300`

3. **测试**: `test_tts_echo_detection.py` (~10 用例)
   - 播放中 ASR 拾取被过滤
   - 播放后 5s 内相似文本被过滤
   - 不同内容不被误过滤
   - Redis 状态标记正确设置/清除

### Phase 3: Frontend Speaker Display (P2)

**目标**: 前端显示说话人标识

**改动文件**:

1. **`VoiceMessageBubble.tsx`** — 新增说话人显示
   - 已识别用户: 头像 + 用户名
   - 未识别用户: 数字标签圆圈 + "用户01"
   - Props 扩展: `speakerInfo?: {userId, label, avatarUrl, isIdentified}`

2. **`voiceStore.ts`** — speaker mapping 管理
   - `speakerMap: Map<string, SpeakerInfo>` — segment_id → speaker info
   - 处理 `speaker.identified` 事件更新 mapping

3. **测试**: 前端组件测试 (~4 用例)

### Phase 4: Unknown Speaker Management (P3)

**目标**: 临时标签持久化和回溯匹配

**改动文件**:

1. **`consumer_events.py`** — 临时标签持久化
   - Redis `voice:unknown_speakers` Hash: `{embedding_hash} → unknown_01`
   - Redis `voice:unknown_counter` — 全局计数器 (INCR)
   - 跨 WebSocket 连接保持一致

2. **`speaker_service.py`** — 注册后回溯匹配
   - `register_speaker()` 完成后，查询所有 `Message.speaker_id` 匹配临时标签的记录
   - 用 Gateway identify 验证 → 匹配成功则更新 Message.speaker_id
   - 清理 Redis 中对应的临时标签

3. **测试**: `test_unknown_speaker_labeling.py` (~7 用例)
   - 临时标签分配一致性
   - 跨连接标签保持
   - 注册后回溯匹配
   - 不同未知说话人获得不同标签

## Risk Mitigation

| 风险 | 影响 | 缓解 |
|------|------|------|
| Gateway identify API 延迟 | 每段增加 ~100ms | 异步调用，不阻塞 ASR 流 |
| 短语音识别不准 | 误识别 | FR-010: < 0.5s 跳过识别 |
| TTS 回声误杀真实语音 | 漏处理用户请求 | 双重验证 + 阈值可调 |
| 功能开关 | 回滚风险 | `VOICE_SPEAKER_IDENTIFICATION_ENABLED=False` 即恢复原行为 |

## Rollout Strategy

1. 默认 `VOICE_SPEAKER_IDENTIFICATION_ENABLED=False`，合并不影响现有行为
2. 先注册 2-3 个家庭成员声纹进行内测
3. 验证 SC-001 (准确率 >= 90%) 后开启
4. 监控 TTS 回声过滤率和误杀率 (SC-002, SC-003)
