# Research: Ambient 模式说话人识别

**Date**: 2026-04-15 | **Branch**: `017-ambient-speaker-id`

## 调研方法

通过 3 个并行 agent 调研现有代码库，覆盖后端语音核心模块、前端语音状态、持久化与设置。

## 决策记录

### 决策 1: Speaker Identification vs Diarization

**Decision**: 使用 Speaker Identification（预注册识别），不使用 Speaker Diarization（盲聚类）

**Rationale**:
- 家庭场景成员已知，识别比聚类准确率高 30%+ (95% vs 60-80%)
- Gateway 已有 `/v1/voice/speakers/identify` API，零新增依赖
- LinChat 已有完整 SpeakerProfile 基础设施
- 短语音（0.5-1s）识别可用，聚类几乎不可用

**Alternatives considered**:
- pyannote-audio 4.0: 需 GPU，无流式，不适合实时家庭场景
- 3D-Speaker CAM++: 中文最优但当前 ECAPA-TDNN 足够，未来备选
- ESP32-S3 边缘计算: 算力不足，无法运行声纹模型

### 决策 2: TTS 回声过滤策略

**Decision**: 软件方案（时间窗口 + 文本相似度双重过滤）

**Rationale**:
- 小爱音箱 TTS 架构下硬件 AEC 不可行（ESP32/XVF3800 不知道 TTS 内容）
- WiFi + 小爱内部 + 声学三重延迟叠加，无法精确对齐硬件参考信号
- 双重验证降低误判率

**Alternatives considered**:
- XVF3800 AEC 硬件回声消除: 需 I2S TX 注入参考信号，开发 5+ 天，ROI 极低
- 单纯时间窗口: 误杀率高（TTS 播放时用户说不同的话会被过滤）
- 单纯文本比对: 延迟高（等 ASR 转录完成才能比对）

### 决策 3: 临时标签生命周期

**Decision**: 永久持久化（Redis Hash），直到注册声纹后替换

**Rationale**:
- 用户 clarify 确认选择 D（永不重置）
- 避免同一访客每次来都被分配新标签
- 注册后全量回溯匹配历史消息

**Alternatives considered**:
- WebSocket 连接级: 断开即丢失，体验差
- 时间窗口级: 增加复杂度，无明显收益
- Ambient 会话级: 定义模糊

### 决策 4: Gateway API 调用模式

**Decision**: 复用现有 `build_gateway_headers()` + httpx async POST

**Rationale**:
- `speaker_service.py` 已有完整的 Gateway 调用模式（注册/删除）
- Bearer token 认证、error handling 模式已验证
- 新增 identify_from_pcm() 遵循同一模式

**Alternatives considered**:
- WebSocket 流式调用: Gateway identify 是单次请求，不需要流式
- 同步调用: 阻塞 ASR 主路径，不可接受

## 已有基础设施清单

| 组件 | 位置 | 状态 | 改动需求 |
|------|------|------|---------|
| SpeakerProfile 模型 | `voice/models.py:5-20` | 生产就绪 | 无 |
| Message.speaker_id | `chat/models.py:23` | CharField, 未填充 | 填充 |
| identify_speaker() | `speaker_service.py:66-72` | 工作中 | 无 |
| Gateway HTTP 模式 | `speaker_service.py:25-54` | 生产就绪 | 复用 |
| PCM Redis 缓存 | `consumer_session.py:217` | 生产就绪 | 无 |
| speaker_identified 参数 | `response_decision_service.py:30` | 已激活 | 无 |
| _speaker_aggregators | `consumer_session.py:76` | 已初始化 | 启用 |
| VOICE_SPEAKER_THRESHOLD | `settings.py:433` | 0.5 | 复用 |
| 前端 currentSpeakerId | `voiceStore.ts:29` | null | 填充 |
| 前端 speaker.identified | `useVoiceWebSocket.ts:97` | 已映射 | 无 |
| 前端 SpeakerProfile 类型 | `voice.ts:22-28` | 已定义 | 无 |
| DiarizeSegment | `speaker_service.py:11-20` | DEPRECATED | 移除 |
| diarize_audio() | `speaker_service.py:95-97` | DEPRECATED | 移除 |

## NEEDS CLARIFICATION 解决状态

无遗留 NEEDS CLARIFICATION — 所有技术决策均已明确。
