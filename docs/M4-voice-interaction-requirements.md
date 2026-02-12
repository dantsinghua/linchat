# LinChat 语音交互需求文档 (M4)

> **版本**: v1.0
> **日期**: 2026-02-12
> **状态**: Draft
> **前置依赖**: M3 消息聚合、llmgateway 语音能力
> **关联文档**: llmgateway/docs/voice-capability-requirements.md

---

## 1. 概述

### 1.1 背景

LinChat 作为贾维斯式智能助手，需要支持语音交互能力，实现：
- 用户可以通过语音与助手对话
- 支持随时打断（全双工）
- 家庭场景下区分不同成员
- 7x24 持续监听（树莓派采集端）

### 1.2 目标

1. **语音输入**: 支持 Web 端和树莓派端语音输入
2. **语音输出**: 支持语音回复（MiniCPM-o 或 TTS）
3. **打断机制**: 用户说话时自动停止 AI 回复
4. **消息聚合**: 整合语音片段为完整语义单元
5. **声纹关联**: 自动识别说话人，关联到对应用户会话
6. **记忆生成**: 语音对话记录用于每日记忆总结

### 1.3 系统架构

```
┌─────────────────────────────────────────────────────────────────────┐
│                           终端设备                                   │
│  ┌─────────────────┐              ┌─────────────────────────────┐  │
│  │   树莓派采集端   │              │       Web 客户端            │  │
│  │  ┌───────────┐  │              │  ┌─────────┐  ┌─────────┐  │  │
│  │  │ USB 麦克风 │  │              │  │   PC    │  │  手机   │  │  │
│  │  │  (7x24)   │  │              │  │ 浏览器  │  │ 浏览器  │  │  │
│  │  └─────┬─────┘  │              │  └────┬────┘  └────┬────┘  │  │
│  │        │        │              │       │            │       │  │
│  │  ┌─────▼─────┐  │              │       └──────┬─────┘       │  │
│  │  │ VAD 本地  │  │              │              │             │  │
│  │  │ 预处理    │  │              │       WebRTC/WebSocket     │  │
│  │  └─────┬─────┘  │              │              │             │  │
│  └────────┼────────┘              └──────────────┼─────────────┘  │
│           │                                      │                 │
│           └──────────────┬───────────────────────┘                 │
│                          │                                         │
│                   WebSocket 双工连接                                │
└──────────────────────────┼─────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────────┐
│                        LinChat 后端                                  │
│                                                                     │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │                    语音交互层 (新增)                          │   │
│  │  ┌─────────┐  ┌─────────┐  ┌─────────┐  ┌─────────────────┐ │   │
│  │  │ 音频流  │  │ 打断    │  │ 语音    │  │ 声纹关联        │ │   │
│  │  │ 接收器  │─▶│ 控制器  │─▶│ 聚合器  │─▶│ (→user_id)     │ │   │
│  │  └─────────┘  └─────────┘  └─────────┘  └────────┬────────┘ │   │
│  └─────────────────────────────────────────────────┼───────────┘   │
│                                                    │               │
│  ┌─────────────────────────────────────────────────▼───────────┐   │
│  │                    消息聚合层 (M3 扩展)                       │   │
│  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐  │   │
│  │  │ 文本消息    │  │ 语音消息    │  │ 响应决策器          │  │   │
│  │  │ 缓冲区      │  │ 缓冲区      │  │ (何时回复/跳过)     │  │   │
│  │  └──────┬──────┘  └──────┬──────┘  └──────────┬──────────┘  │   │
│  │         └────────────────┴────────────────────┘             │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                                    │                               │
│  ┌─────────────────────────────────▼───────────────────────────┐   │
│  │                    Agent 层 (现有)                           │   │
│  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐  │   │
│  │  │ LangGraph   │  │ SubAgents   │  │ 工具调用            │  │   │
│  │  │ Agent       │  │             │  │                     │  │   │
│  │  └─────────────┘  └─────────────┘  └─────────────────────┘  │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                                    │                               │
└────────────────────────────────────┼───────────────────────────────┘
                                     │
                            HTTP (OpenAI 兼容)
                                     │
                                     ▼
                        ┌────────────────────────┐
                        │      llmgateway        │
                        │  ┌──────┐ ┌─────────┐  │
                        │  │ VAD  │ │MiniCPM-o│  │
                        │  └──────┘ └─────────┘  │
                        │  ┌──────┐ ┌─────────┐  │
                        │  │声纹ID│ │ ASR/TTS │  │
                        │  └──────┘ └─────────┘  │
                        └────────────────────────┘
```

---

## 2. 功能需求

### 2.1 语音输入接口

#### 2.1.1 WebSocket 端点

```yaml
WebSocket: /ws/voice/{user_id}

# 客户端 → 服务器
Message Types:
  - type: "audio_chunk"
    data:
      audio: string (base64, PCM 16kHz 16bit mono)
      timestamp_ms: int
      
  - type: "control"
    data:
      action: "start" | "stop" | "interrupt"

# 服务器 → 客户端
Message Types:
  - type: "vad_result"
    data:
      is_speech: bool
      speech_prob: float
      
  - type: "transcript"  # 方案 A
    data:
      text: string
      is_final: bool
      speaker_id: string | null
      
  - type: "response_audio"
    data:
      audio: string (base64)
      is_final: bool
      
  - type: "response_text"
    data:
      text: string
      is_final: bool
      
  - type: "state"
    data:
      state: "listening" | "processing" | "speaking" | "interrupted"
```

#### 2.1.2 REST 端点（简单场景）

```yaml
POST /api/v1/voice/send
Content-Type: multipart/form-data

Parameters:
  - audio: binary (完整语音文件)
  - user_id: string (可选，声纹自动识别)
  
Response:
  - message_id: string
  - transcript: string
  - speaker_id: string
  - response_text: string
  - response_audio_url: string (MinIO 预签名 URL)
```

---

### 2.2 打断机制 (Full-Duplex Interruption)

**核心逻辑**: 用户开始说话时，立即停止 AI 回复

#### 状态机

```
                 ┌─────────────────────┐
                 │                     │
                 ▼                     │
        ┌────────────────┐             │
        │   LISTENING    │◀────────────┤
        │  (等待用户说话) │             │
        └───────┬────────┘             │
                │ VAD 检测到语音       │
                ▼                      │
        ┌────────────────┐             │
        │   RECEIVING    │             │
        │  (接收语音中)   │             │
        └───────┬────────┘             │
                │ VAD 检测到静音       │
                │ (超过阈值)           │
                ▼                      │
        ┌────────────────┐             │
        │   PROCESSING   │             │
        │  (处理中)       │             │
        └───────┬────────┘             │
                │ 开始生成回复         │
                ▼                      │
        ┌────────────────┐             │
  ┌────▶│   SPEAKING     │─────────────┘
  │     │  (AI 说话中)    │  回复完成
  │     └───────┬────────┘
  │             │ VAD 检测到用户语音
  │             ▼
  │     ┌────────────────┐
  └─────│  INTERRUPTED   │
        │  (被打断)       │
        └────────────────┘
```

#### 打断处理流程

```python
# 伪代码
async def handle_interrupt(session: VoiceSession):
    # 1. 立即停止当前 TTS 输出
    await session.stop_audio_output()
    
    # 2. 取消正在进行的 LLM 调用（如果有）
    if session.pending_llm_task:
        session.pending_llm_task.cancel()
    
    # 3. 保存被打断的上下文
    session.interrupted_response = session.current_response
    
    # 4. 切换到 RECEIVING 状态
    session.state = VoiceState.RECEIVING
    
    # 5. 通知前端
    await session.send_state("interrupted")
```

---

### 2.3 语音消息聚合

**目的**: 将连续的语音片段聚合为完整的语义单元

#### 聚合策略

```python
class VoiceAggregator:
    """语音消息聚合器
    
    策略:
    1. 静音阈值 — VAD 检测到连续 N 秒静音后触发处理
    2. 最大时长 — 单次语音不超过 M 秒
    3. 语义完整 — 检测到句号/问号等结束标记 (方案 A)
    4. 紧急词汇 — "停"、"取消"、"闭嘴" 立即响应
    """
    
    # 可配置参数
    SILENCE_THRESHOLD_MS = 1500    # 静音阈值 (1.5秒)
    MAX_DURATION_MS = 30000        # 最大单次时长 (30秒)
    URGENT_KEYWORDS = ["停", "取消", "闭嘴", "stop", "cancel"]
```

#### 聚合流程

```
音频流 ──▶ VAD ──▶ 语音片段缓冲 ──▶ 聚合判断 ──▶ 声纹识别 ──▶ 发送处理
                        │                │
                        │                ├── 静音超时 → 触发
                        │                ├── 最大时长 → 触发
                        │                └── 紧急词汇 → 立即触发
                        │
                        └── 方案 A: 同时做流式 ASR，用于紧急词汇检测
```

---

### 2.4 声纹关联

**目的**: 识别说话人，关联到对应用户的会话记录

#### 流程

```
1. 用户注册声纹 (设置页面)
   ├── 录制 10-30 秒语音
   ├── 调用 llmgateway /v1/audio/speakers/register
   └── 存储 speaker_id ↔ user_id 映射

2. 实时识别 (语音输入时)
   ├── 取语音片段前 3 秒
   ├── 调用 llmgateway /v1/audio/speakers/identify
   ├── 获取 speaker_id → user_id
   └── 关联到对应用户会话

3. 未识别处理
   ├── confidence < threshold → 标记为 "unknown_speaker"
   ├── 记录到单独的 unknown 会话
   └── 管理员可后续归档
```

#### 数据模型扩展

```python
# apps/users/models.py
class SpeakerProfile(models.Model):
    """用户声纹配置"""
    user = models.OneToOneField(SysUser, on_delete=models.CASCADE)
    speaker_id = models.CharField(max_length=64, unique=True)  # llmgateway 返回
    name = models.CharField(max_length=128)  # 显示名称
    enrolled_at = models.DateTimeField(auto_now_add=True)
    
# apps/chat/models.py  
class Message(models.Model):
    # ... 现有字段 ...
    
    # 新增语音相关字段
    audio_url = models.URLField(null=True, blank=True)  # MinIO URL
    audio_duration = models.FloatField(null=True, blank=True)
    speaker_id = models.CharField(max_length=64, null=True, blank=True)
    is_voice = models.BooleanField(default=False)
```

---

### 2.5 响应决策

**目的**: 智能判断何时回复、何时保持沉默

#### 决策规则

```python
class ResponseDecider:
    """响应决策器
    
    判断输入是否需要 AI 回复
    """
    
    async def should_respond(self, context: VoiceContext) -> Decision:
        # 1. 紧急命令 — 立即响应
        if self._is_urgent_command(context.text):
            return Decision.RESPOND_IMMEDIATELY
        
        # 2. 明确问句 — 需要响应
        if self._is_question(context.text):
            return Decision.RESPOND
        
        # 3. 唤醒词 — 需要响应
        if self._has_wake_word(context.text):
            return Decision.RESPOND
        
        # 4. 对话中的陈述 — 可能需要响应
        if context.in_active_conversation:
            return Decision.RESPOND
        
        # 5. 背景对话（非针对 AI）— 不响应，但记录
        if self._is_background_chat(context):
            return Decision.RECORD_ONLY
        
        # 6. 默认 — 不确定时询问确认
        return Decision.ASK_CONFIRMATION
    
    def _has_wake_word(self, text: str) -> bool:
        """检测唤醒词"""
        wake_words = ["小鱼", "贾维斯", "助手", "hey", "hi"]
        return any(w in text.lower() for w in wake_words)
```

#### 决策结果

| 决策 | 行为 |
|------|------|
| RESPOND_IMMEDIATELY | 立即回复，打断当前处理 |
| RESPOND | 正常回复 |
| RECORD_ONLY | 不回复，但记录到会话历史（用于记忆） |
| ASK_CONFIRMATION | 回复 "你是在叫我吗？" |
| IGNORE | 完全忽略（噪音等） |

---

### 2.6 数据存储与记忆

**目的**: 语音对话存入 PostgreSQL，用于每日记忆生成

#### 存储流程

```
语音输入
    │
    ▼
┌────────────────┐
│ 声纹识别       │ ──▶ user_id
└───────┬────────┘
        │
        ▼
┌────────────────┐
│ 语音转文本     │ ──▶ transcript
│ (ASR/MiniCPM-o)│
└───────┬────────┘
        │
        ▼
┌────────────────┐
│ 存储 Message   │
│ - user_id      │
│ - content      │
│ - audio_url    │
│ - is_voice=True│
│ - speaker_id   │
└───────┬────────┘
        │
        ▼
┌────────────────────────────┐
│ 每日记忆任务 (Celery Beat) │
│ - 聚合当日语音对话         │
│ - 生成用户记忆摘要         │
│ - 存入 user_memory 表      │
└────────────────────────────┘
```

---

## 3. 非功能需求

### 3.1 延迟要求

| 环节 | 目标延迟 | 说明 |
|------|----------|------|
| VAD 检测 | < 50ms | 实时 |
| 打断响应 | < 100ms | 停止播放延迟 |
| 首字节响应 | < 1s | 用户说完到 AI 开始回复 |
| 端到端 | < 2s | 完整一轮对话 |

### 3.2 音频质量

- 采样率: 16kHz
- 位深: 16bit
- 声道: Mono
- 格式: PCM (传输) / WAV (存储)

### 3.3 并发支持

- 家庭场景: 1-3 个并发语音流
- Web 客户端: 多 Tab 共享同一用户会话

---

## 4. 接口汇总

### 4.1 WebSocket 端点

| 端点 | 说明 |
|------|------|
| `/ws/voice/{user_id}` | 语音交互主通道 |
| `/ws/voice/raspberry` | 树莓派专用（自动声纹识别） |

### 4.2 REST 端点

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/v1/voice/send` | POST | 发送语音消息（非流式） |
| `/api/v1/voice/speakers` | GET | 获取已注册声纹列表 |
| `/api/v1/voice/speakers/register` | POST | 注册新声纹 |
| `/api/v1/voice/speakers/{id}` | DELETE | 删除声纹 |
| `/api/v1/voice/settings` | GET/PUT | 语音设置（唤醒词等） |

### 4.3 前端组件

| 组件 | 说明 |
|------|------|
| `VoiceButton` | 语音输入按钮（按住说话/点击切换） |
| `VoiceWaveform` | 音频波形可视化 |
| `SpeakerEnrollment` | 声纹注册向导 |
| `VoiceSettings` | 语音设置面板 |

---

## 5. 树莓派采集端

### 5.1 架构

```
┌────────────────────────────────────────┐
│            树莓派 (Python)              │
│                                        │
│  ┌─────────┐   ┌─────────────────────┐ │
│  │USB 麦克风│──▶│ 音频采集 (PyAudio)  │ │
│  └─────────┘   └──────────┬──────────┘ │
│                           │            │
│                ┌──────────▼──────────┐ │
│                │ 本地 VAD (Silero)   │ │
│                │ - 降低网络流量       │ │
│                │ - 只传有声音的片段   │ │
│                └──────────┬──────────┘ │
│                           │            │
│                ┌──────────▼──────────┐ │
│                │ WebSocket Client    │ │
│                │ → linchat 后端      │ │
│                └─────────────────────┘ │
└────────────────────────────────────────┘
```

### 5.2 配置文件

```yaml
# raspberry_config.yaml
server:
  url: "wss://linchat.example.com/ws/voice/raspberry"
  token: "raspberry_device_token"

audio:
  device: "default"  # 或指定设备 ID
  sample_rate: 16000
  chunk_ms: 30

vad:
  enabled: true
  model_path: "./models/silero_vad.onnx"
  threshold: 0.5
  min_speech_duration_ms: 250
  min_silence_duration_ms: 1500

speaker:
  # 树莓派不做声纹识别，交给服务端
  identify_on_server: true
```

---

## 6. 实施计划

### Phase 1: 基础架构 (1 周)
- [ ] WebSocket 端点实现
- [ ] VoiceSession 状态管理
- [ ] Message 模型扩展 (audio_url, is_voice)

### Phase 2: 语音处理集成 (1 周)
- [ ] llmgateway 客户端封装
- [ ] VAD 集成
- [ ] MiniCPM-o 语音调用

### Phase 3: 打断与聚合 (1 周)
- [ ] 打断机制实现
- [ ] VoiceAggregator 实现
- [ ] ResponseDecider 实现

### Phase 4: 声纹与记忆 (1 周)
- [ ] SpeakerProfile 模型
- [ ] 声纹注册/识别流程
- [ ] 每日语音记忆任务

### Phase 5: 前端与树莓派 (1 周)
- [ ] Web 语音组件
- [ ] 树莓派采集程序

---

## 7. 与 M3 消息聚合的关系

M4 语音交互模块是 M3 消息聚合的**扩展和特化**：

| 维度 | M3 消息聚合 | M4 语音交互 |
|------|-------------|-------------|
| 输入类型 | 文本消息 | 音频流 |
| 聚合触发 | 时间窗口 + 主动触发 | VAD 静音检测 + 时间窗口 |
| 打断 | 无 | 全双工打断 |
| 用户识别 | session/token | 声纹识别 |
| 存储 | PostgreSQL | PostgreSQL + MinIO |

**建议**: M3 和 M4 共享聚合框架，通过策略模式区分文本/语音处理。

---

## 8. 参考资料

- [CleanS2S 架构](https://github.com/opendilab/CleanS2S) — 全双工语音交互参考
- [CleanS2S 论文](https://arxiv.org/pdf/2506.01268) — 主观行动判断
- llmgateway/docs/voice-capability-requirements.md — 模型服务层需求
- M3 消息聚合需求（待编写）
