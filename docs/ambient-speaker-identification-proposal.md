# LinChat Ambient 模式说话人识别可行性方案

> **状态**: 待审批
> **日期**: 2026-04-15
> **调研范围**: 10 路并行调研（LinChat 代码、LLM Gateway、ESP32-S3、pyannote-audio、wespeaker、3D-Speaker、NeMo、开源 diarization 方案、ReSpeaker 硬件、ESP32 边缘计算）

---

## 一、核心结论：问题重定义

### 1.1 之前 diarize 失败的根本原因

之前的 diarization 代码（`consumer_events.py` 和 `speaker_service.py` 中已标记 DEPRECATED）效果差，**不是模型差，而是选错了问题类型**。

| 对比维度 | Speaker Diarization（盲聚类） | Speaker Identification（预注册识别） |
|---------|------|------|
| 前置条件 | 无需预注册 | 需要预注册声纹 |
| 输出 | "Speaker_0, Speaker_1..."（匿名） | "安琳, 团子爸爸"（具名） |
| 算法难度 | 极高（无监督聚类） | **低得多**（余弦相似度匹配） |
| 短语音（<1s）适应性 | 差（几乎不可用） | 较好（0.5-1s 可用） |
| 真实家庭环境准确率 | **60-80%**（远场+噪声+短语句叠加） | **95%+** |
| 适合场景 | 会议/播客/呼叫中心 | **智能家居（已知家庭成员）** |

### 1.2 LinChat 已具备的条件

LinChat **已有完整的声纹基础设施**，只是从未在 ambient 路径中启用：

| 已有能力 | 文件 | 状态 |
|---------|------|------|
| SpeakerProfile 数据模型 | `voice/models.py` | 生产就绪 |
| Gateway 声纹注册 API | `POST /v1/voice/speakers` | 生产就绪 |
| Gateway 声纹识别 API | `POST /v1/voice/speakers/identify` | 生产就绪 |
| Gateway 批量 diarize API | `POST /v1/voice/diarize` | 已实现（VAD→嵌入→聚类→ASR） |
| speaker_service.identify_speaker() | `voice/services/speaker_service.py` | 已实现但从未调用 |
| Per-speaker 聚合器基础设施 | `consumer_events.py:140-148` | 代码存在但未启用 |
| 前端 currentSpeakerId 状态 | `voiceStore.ts` | 字段存在但从未填充 |
| PCM 音频缓存 | Redis `voice:audio_chunks:{uid}:{seg}` | 生产就绪 |

**结论：不需要引入新的 ML 框架，利用 Gateway 已有能力 + LinChat 已有基础设施即可实现。**

---

## 二、方案对比与选型

### 2.1 三个方向评估

#### 方向 1：ESP32-S3 边缘计算

| 能力 | 可行性 | 说明 |
|------|--------|------|
| VAD（语音活动检测） | 可行 | VADNet（ESP-SR 原生），可减少 70-90% 传输 |
| 唤醒词检测 | 可行 | WakeNet9，<200ms 延迟 |
| 简单命令词 | 可行 | MultiNet7，最多 200-300 条 |
| **通用 ASR** | **不可行** | 模型太大（Whisper Tiny 75MB），超出 512KB SRAM |
| **声纹识别** | **不可行** | ECAPA-TDNN 6-24MB，无端侧方案 |
| **说话人分离** | **不可行** | 需要嵌入提取+聚类，算力不足 |

**结论：ESP32-S3 不能做声纹识别。当前"采集+传输"架构正确。可选优化：端侧 VAD 过滤。**

#### 方向 2：LLM Gateway 一体化（推荐）

Gateway 已具备完整的语音处理链路：

| 能力 | API | 模型 | 状态 |
|------|-----|------|------|
| ASR 流式转录 | `WS /v1/audio/transcriptions/stream` | Paraformer-zh (sherpa_onnx) | 生产就绪 |
| VAD | `WS /v1/voice/vad/stream` | Silero VAD | 生产就绪 |
| 声纹注册 | `POST /v1/voice/speakers` | ECAPA-TDNN (speechbrain) | 生产就绪 |
| **声纹识别** | `POST /v1/voice/speakers/identify` | ECAPA-TDNN, 192-d, pgvector cosine | **生产就绪** |
| 批量 diarize | `POST /v1/voice/diarize` | VAD→嵌入→层次聚类→ASR | 已实现 |
| TTS | `WS /v1/audio/speech/stream` | Kokoro-82M | 生产就绪 |

**结论：Gateway 的 `/v1/voice/speakers/identify` 已经能解决核心问题。只需在 LinChat 后端的 ambient 路径中调用它。**

#### 方向 3：开源框架集成

| 框架 | 中文能力 | 推理速度 | 部署复杂度 | 是否必要 |
|------|---------|---------|-----------|---------|
| 3D-Speaker (CAM++) | 最佳 | RTF 0.03 | 中 | Gateway 已有 ECAPA-TDNN，暂不需要 |
| pyannote-audio 4.0 | 一般 | 需 GPU | 高 | 不需要（非盲聚类场景） |
| wespeaker | 好 | 中 | 中 | 不需要 |
| NeMo (TitaNet) | 一般 | 中 | 高 | 不需要 |

**结论：当前阶段不需要引入额外 ML 框架。如果未来 Gateway ECAPA-TDNN 的中文识别准确率不足，可考虑替换为 3D-Speaker 的 CAM++（7.2M 参数，EER 0.65%，中文最优，与 FunASR 同源）。**

### 2.2 最终选型

**方案：Gateway Speaker Identify + LinChat 后端集成**

- 零新依赖，零新模型部署
- 复用 Gateway 已有 ECAPA-TDNN + pgvector
- 复用 LinChat 已有 SpeakerProfile + speaker_service
- 改动范围最小，风险最低

---

## 三、推荐架构

### 3.1 数据流

```
reSpeaker XVF3800 (AEC + Beamforming + 降噪)
    ↓ I2S → ESP32-S3 → UDP/WiFi
    ↓
bridge.py → WebSocket
    ↓
VoiceConsumer (LinChat 后端)
    ├── ASR 流式转录 (Gateway, 现有)
    │   → transcription text + segment_id
    │
    ├── 【新增】Speaker Identification (每段语音完成后)
    │   ├── 1. 从 Redis 获取 PCM chunks (voice:audio_chunks:{uid}:{seg})
    │   ├── 2. 拼接 PCM → base64 编码
    │   ├── 3. POST Gateway /v1/voice/speakers/identify
    │   │     → {identified: bool, speaker_id: str, confidence: float}
    │   ├── 4a. 已识别 → 查 SpeakerProfile → 获取 user_id
    │   ├── 4b. 未识别 → 分配临时标签 "unknown_01"
    │   └── 5. 将 speaker_user_id 传递给下游
    │
    ├── UtteranceAggregator (按 speaker_user_id 分组聚合)
    │   └── 【修改】使用已有的 _speaker_aggregators 基础设施
    │
    ├── ResponseDecisionService (决策链)
    │   └── 【新增】TTS 回声检测（第 0 级，最高优先）
    │
    └── VoicePipeline → Agent → TTS
        └── 【修改】使用识别到的 user_id 而非 WebSocket 连接的 user_id
```

### 3.2 TTS 回声过滤（防止循环浪费 token）

小爱音箱或其他 TTS 发声的回复内容也会被 ASR 识别，需要过滤。

**方案：时间窗口 + 文本比对双重过滤**

```python
# 在 ResponseDecisionService.decide() 中新增第 0 级判断

class ResponseDecisionService:
    async def decide(self, text, speaker_id, user_id, mode, speaker_identified):
        # 【新增】Level 0: TTS 回声检测
        if await self._is_tts_echo(text, user_id):
            return DecisionResult.DISCARD, "tts_echo_detected"

        # 现有 Level 1-8 不变...

    async def _is_tts_echo(self, text: str, user_id: int) -> bool:
        """检测是否为 TTS 回声"""
        # 策略 1: 时间窗口 — TTS 播放期间的 ASR 结果大概率是回声
        tts_active = await redis.get(f"voice:tts_playing:{user_id}")
        if tts_active:
            return True

        # 策略 2: 文本相似度 — 与最近 TTS 输出文本比对
        recent_tts = await redis.lrange(f"voice:tts_history:{user_id}", 0, 4)
        for tts_text in recent_tts:
            similarity = _text_similarity(text, tts_text.decode())
            if similarity > 0.7:  # 阈值可调
                return True

        return False
```

**TTS 播放状态标记**（在 TTS 路由中添加）：

```python
# tts_router.py 中，TTS 开始/结束时设置 Redis 标记
async def send_to_ha_speaker(self, user_id, audio_data):
    await redis.setex(f"voice:tts_playing:{user_id}", 30, "1")  # TTS 开始
    # ... 发送 TTS ...
    await redis.delete(f"voice:tts_playing:{user_id}")  # TTS 结束

    # 同时记录 TTS 文本用于文本比对
    await redis.lpush(f"voice:tts_history:{user_id}", tts_text)
    await redis.ltrim(f"voice:tts_history:{user_id}", 0, 9)  # 保留最近 10 条
    await redis.expire(f"voice:tts_history:{user_id}", 300)  # 5 分钟过期
```

### 3.3 未注册用户处理

| 场景 | 处理 |
|------|------|
| Gateway 识别成功（confidence >= 阈值） | 映射到 LinChat user_id，显示用户名+头像 |
| Gateway 识别失败 | 分配临时标签 `unknown_01/02/03...`（按出现顺序） |
| 之后某用户注册声纹，匹配到 unknown_02 | 回溯替换历史消息的 speaker 标签 |

**临时标签管理**：

```python
# consumer_events.py 中
class EventMixin:
    def __init__(self):
        self._unknown_speakers = {}  # embedding_hash → "unknown_01"
        self._unknown_counter = 0

    async def _identify_or_assign(self, pcm_data: bytes) -> tuple[int | None, str]:
        """识别说话人或分配临时标签"""
        result = await gateway_identify(pcm_data)

        if result["identified"]:
            profile = await SpeakerProfile.objects.aget(
                gateway_speaker_id=result["speaker_id"]
            )
            return profile.user_id, profile.name

        # 未识别：用 embedding 指纹区分不同的未知说话人
        # Gateway diarize 返回的 cluster ID 可辅助区分
        emb_hash = result.get("embedding_hash", "default")
        if emb_hash not in self._unknown_speakers:
            self._unknown_counter += 1
            self._unknown_speakers[emb_hash] = f"unknown_{self._unknown_counter:02d}"

        label = self._unknown_speakers[emb_hash]
        return None, label  # user_id=None, label="unknown_01"
```

### 3.4 前端显示

```typescript
// VoiceMessageBubble.tsx 修改

interface VoiceMessageProps {
  message: Message;
  speakerInfo?: {
    userId: number | null;
    label: string;        // "安琳" 或 "unknown_01"
    avatarUrl?: string;   // 已注册用户有头像
    isIdentified: boolean;
  };
}

// 显示逻辑：
// 已识别用户 → 用户头像 + 用户名
// 未识别用户 → 数字标签圆圈（01, 02, 03）+ "用户01"
```

**WebSocket 事件扩展**：

```typescript
// 新增事件类型
interface SpeakerIdentifiedEvent {
  type: 'speaker.identified';
  data: {
    segment_id: string;
    speaker_user_id: number | null;
    speaker_label: string;
    confidence: number;
    is_identified: boolean;
  };
}
```

---

## 四、改动范围评估

### 4.1 后端改动

| 文件 | 改动类型 | 复杂度 | 说明 |
|------|---------|--------|------|
| `consumer_events.py` | 修改 | 中 | `_handle_ambient_transcription` 路径增加 speaker identify 调用 |
| `speaker_service.py` | 修改 | 低 | 取消 DEPRECATED 标记，启用 `identify_speaker()`，增加 PCM identify 方法 |
| `response_decision_service.py` | 修改 | 低 | 增加 Level 0 TTS 回声检测 |
| `tts_router.py` | 修改 | 低 | TTS 播放时设置 Redis 状态标记 |
| `voice_persist_service.py` | 修改 | 低 | record_only_ambient 存储 speaker 信息 |
| `utterance_aggregator.py` | 无改动 | - | 已有 per-speaker 分组能力 |
| `consumer_session.py` | 微调 | 低 | 启用 `_speaker_aggregators` |

### 4.2 前端改动

| 文件 | 改动类型 | 复杂度 | 说明 |
|------|---------|--------|------|
| `voiceStore.ts` | 修改 | 低 | 填充 `currentSpeakerId`，增加 speaker mapping |
| `useVoiceWebSocket.ts` | 修改 | 低 | 处理 `speaker.identified` 事件 |
| `VoiceMessageBubble.tsx` | 修改 | 中 | 显示说话人标识（头像/数字标签） |
| `types/voice.ts` | 修改 | 低 | 增加 SpeakerIdentifiedEvent 类型 |

### 4.3 Gateway 改动

**无改动**。所有需要的 API 已就绪。

### 4.4 不需要改动的部分

- ESP32-S3 固件（保持现有"采集+传输"架构）
- bridge.py（保持现有音频转发逻辑）
- LLM Gateway（所有 API 已就绪）
- 数据库模型（SpeakerProfile 已有所需字段）

---

## 五、实施计划

### Phase 1: 核心 Speaker Identification（2-3 天）

1. **取消 speaker_service.py DEPRECATED 标记**
   - 启用 `identify_speaker()` 方法
   - 新增 `identify_from_pcm(pcm_data: bytes)` 方法，调用 Gateway API

2. **修改 consumer_events.py ambient 路径**
   - 在 `transcription.completed` 事件后，取 PCM chunks 调用 speaker identify
   - 使用已有的 `_speaker_aggregators` 按 speaker_user_id 分组
   - 将 speaker 信息传递给 ResponseDecisionService

3. **前端显示说话人标识**
   - 处理 `speaker.identified` WebSocket 事件
   - VoiceMessageBubble 显示头像/数字标签

### Phase 2: TTS 回声过滤（1 天）

4. **ResponseDecisionService 增加 Level 0**
   - TTS 播放状态 Redis 标记
   - 文本相似度比对
   - 新增 `DISCARD` 决策结果

5. **tts_router.py 标记 TTS 播放状态**

### Phase 3: 未知说话人管理（1 天）

6. **临时标签分配逻辑**
   - unknown_01/02/03 按出现顺序分配
   - 基于 Gateway 返回的 embedding 区分不同未知说话人

7. **声纹注册后回溯匹配**
   - 新用户注册声纹时，检查近期 unknown 消息
   - 匹配到的历史消息更新 speaker 信息

### Phase 4: 优化与调参（1 天）

8. **识别阈值调参**
   - Gateway 默认阈值 0.6，根据实际家庭成员测试调整
   - 建议在 VoiceSettings 中增加可配置阈值

9. **性能优化**
   - embedding 内存缓存（避免每次查 pgvector）
   - 识别结果短期缓存（同一 segment 不重复识别）

---

## 六、未来扩展（不在本期范围）

| 扩展方向 | 触发条件 | 方案 |
|---------|---------|------|
| Gateway ECAPA-TDNN 中文准确率不足 | 实测 < 90% | 替换为 3D-Speaker CAM++（7.2M，EER 0.65%，中文最优） |
| 需要端侧 VAD 减少传输 | 带宽/功耗敏感 | ESP32-S3 固件改为 ESP-IDF + VADNet |
| 需要流式 diarization | 访客场景频繁 | 引入 diart（基于 pyannote 的流式包装） |
| 需要端侧唤醒词 | 用户体验优化 | ESP32-S3 + WakeNet9 |

---

## 七、风险与缓解

| 风险 | 影响 | 缓解措施 |
|------|------|---------|
| Gateway identify API 延迟 | 每段语音增加 ~100ms | 异步调用，不阻塞 ASR 流 |
| 短语音（<0.5s）识别准确率低 | 短感叹词可能误识别 | 设置最小音频长度阈值（0.5s），过短的不做识别 |
| ECAPA-TDNN 中文准确率未知 | 可能需要换模型 | Phase 4 调参验证，备选 CAM++ |
| TTS 回声检测不精准 | 可能误过滤真实语音 | 双重验证（时间窗口 + 文本相似度），阈值可调 |
| 多人同时说话 | 重叠语音识别困难 | XVF3800 波束成形已做分离，取最强波束即可 |

---

## 八、测试计划

### 单元测试

- `test_speaker_identification.py`: identify_from_pcm 正确调用 Gateway API
- `test_tts_echo_detection.py`: 时间窗口 + 文本比对过滤逻辑
- `test_unknown_speaker_labeling.py`: 临时标签分配和去重

### 集成测试

- 注册 2-3 个家庭成员声纹 → ambient 模式对话 → 验证识别结果
- TTS 播放期间说话 → 验证回声过滤
- 未注册访客说话 → 验证 unknown 标签分配

### E2E 验证

- 全链路：reSpeaker → ASR → Speaker ID → 前端显示
- 连续对话 10 分钟，验证准确率和稳定性

---

## 附录 A：开源框架调研摘要

### pyannote-audio 4.0

- 最佳开源 DER（11.2%），VBx 聚类
- 无流式支持，需 GPU，需 HuggingFace Token
- 适合离线会议场景，**不适合 LinChat 实时家庭场景**

### 3D-Speaker (CAM++)

- 中文最优（200K+ 中文说话人数据）
- RTF 0.03（业界最快），7.2M 参数
- 完整 ONNX 导出 + C++ 推理引擎
- Apache 2.0，与 FunASR 同源（阿里生态）
- **如需替换 Gateway ECAPA-TDNN 的首选方案**

### wespeaker

- 18+ 模型，ONNX/TensorRT/MNN 多后端
- 清晰的 Python API（`wespeaker.load_model('chinese')`）
- UMAP + HDBSCAN 聚类
- **适合作为独立说话人工具包**

### NVIDIA NeMo

- TitaNet-Large 23M 参数，VoxCeleb1 EER 0.66%
- Streaming Sortformer（2025.08 发布）
- 重框架依赖，部署复杂
- **过重，不适合 LinChat 场景**

### ESP32-S3

- 能做：VAD（VADNet）、唤醒词（WakeNet）、命令词（MultiNet）
- 不能做：通用 ASR、声纹识别、说话人分离
- **当前"采集+传输"架构正确，无需改变**

### NVIDIA NeMo（不采用）

- **严重 overkill**：ClusteringDiarizer 和 Sortformer 为会议/呼叫中心设计，家庭 2-5 人场景大材小用
- **依赖过重**：`nemo_toolkit[all]` 2-4GB，与 Django + LangGraph 技术栈冲突
- **无中文验证**：TitaNet-Large 仅在英文数据集训练（VoxCeleb/Fisher/Switchboard），中文 EER 退化 30-50%
- **无法轻量提取**：单独使用 TitaNet 仍需 `nemo_toolkit[asr]`（~1.5GB）
- Gateway 已有 ECAPA-TDNN（192 维，已验证 quality_score=0.88），无需替换
- 如需提升精度，优先选 3D-Speaker CAM++（中文原生、7.2M 参数）而非 NeMo

### ReSpeaker XVF3800 硬件辅助（当前阶段不改动）

**DOA（方向到达角）能力**：
- XVF3800 维护 4 个波束（focused beam 1/2 + free-running + auto-selected）
- `AEC_AZIMUTH_VALUES` 可读取 4 个波束方位角（0-360 度）
- 精度约 20-45 度（4 麦圆阵列，受反射影响）
- **当前 I2S 架构无法读取 DOA**（需 USB/I2C 控制接口），获取需改固件

**AEC 回声消除用于 TTS 检测**：
- XVF3800 AEC 需要参考信号通过 I2S DATA_IN 注入
- **当前小爱音箱 TTS 架构下不可行**：TTS 音频从小爱音箱扬声器直接播放，ESP32/XVF3800 完全不知道 TTS 内容
- WiFi 延迟 + 小爱内部延迟 + 声学延迟三重叠加，无法精确对齐
- **软件方案（时间窗口 + 文本比对）远优于硬件 AEC**

**多通道输出**：
- I2S 标准模式仅 2 通道（左+右），已用右声道输出 ASR beam
- 获取更多通道需切换 USB 6ch 固件（丧失无线部署）或 Packed 模式（精度下降）

**ROI 评估**：

| 方案 | 开发量 | 收益 | ROI |
|------|--------|------|-----|
| A: 维持现状（推荐） | 0 | 声纹识别足够 | -- |
| B: I2C 读 DOA/spenergy | 3-5 天 | DOA 辅助区分 | **低** |
| C: 左声道改 focused beam | 1-2 天 | 两路波束音频 | **中低** |
| D: USB 6ch 固件 | 5+ 天 | 全部通道 | **极低** |
| E: I2S TX 注入 AEC 参考 | 5+ 天 | 硬件回声消除 | **极低** |

**结论：维持方案 A，Gateway 声纹识别上线后若准确率不足，再考虑方案 C（最小改动的硬件辅助）。**

---

## 附录 B：改动影响分析摘要

### 对现有功能的影响

| 功能模块 | 影响级别 | 说明 |
|---------|---------|------|
| voice_chat 模式 | **无影响** | 所有改动在 `if mode == "ambient"` 分支内 |
| ambient 基础流程 | 低 | 新增识别步骤，异常时降级到现有逻辑 |
| 8 级决策链 | 无 | `speaker_identified` 参数已预留 |
| 数据库 | **无迁移** | `Message.speaker_id` 字段已存在 |

### 代码改动量

- 后端：7 个文件修改，~103 行实际改动
- 前端：4 个文件修改，~50 行实际改动
- Gateway/固件：**零改动**
- 新增测试：4 新建 + 2 扩展 = **29 个新用例**

### 灰度与回滚

```python
# settings.py
VOICE_SPEAKER_IDENTIFICATION_ENABLED = env.bool("VOICE_SPEAKER_IDENTIFICATION_ENABLED", False)
```

关闭开关即恢复原有行为，零数据迁移。

---

*本方案基于 12 路并行调研结果综合生成，待安琳审批后方可进入实施阶段。*
