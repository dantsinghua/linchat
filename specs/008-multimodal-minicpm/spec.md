# Feature Specification: 全模态模型接入 (MiniCPM-V/o)

**Feature Branch**: `008-multimodal-minicpm`
**Created**: 2026-02-06
**Status**: Draft
**Input**: User description: "接入全模态模型 MiniCPM-V/o，支持图像理解、视频分析、语音交互等多模态能力"

## Architecture Overview

### 网关统一调用架构

本特性通过 **LLM Gateway（模型网关）** 统一访问 MiniCPM 多模态模型服务。网关负责：
- 统一管理所有私有化本地部署的模型服务
- 提供标准化的 OpenAI 兼容 API 接口
- 处理模型路由、负载均衡和故障转移
- 支持流式响应和推理取消

```
LinChat 前端
     │
     ▼
LinChat 后端 (Django + LangGraph)
     │
     ▼
LLM Gateway (模型网关)
     │
     ├─► 纯文本模型 (DeepSeek, Qwen 等)
     │
     └─► 多模态模型 (MiniCPM-V/o)
              │
              ├─► 视觉理解 (图像/视频/文档)
              └─► 语音交互 (ASR/TTS)
```

### 网关接口规范

参考现有的 Document Parse API 设计风格，网关需提供以下标准接口：
- **多模态聊天接口**：支持图像、视频、音频输入的流式对话
- **推理取消接口**：支持用户主动终止正在进行的推理任务
- ~~**任务状态查询**~~：[已移除] 单用户场景不需要（宪法 9.2）

## User Scenarios & Testing *(mandatory)*

### User Story 1 - 图像理解对话 (Priority: P1)

用户在聊天界面上传一张图片，AI 能够理解图片内容并回答关于图片的问题。用户可以进行多轮对话，持续追问图片相关的细节。

**Why this priority**: 图像理解是多模态的核心能力，是最常见的使用场景，也是验证整个多模态架构的基础功能。

**Independent Test**: 用户上传任意图片并提问"这张图片里有什么？"，系统返回准确的图片描述，即可验证基本功能可用。

**Acceptance Scenarios**:

1. **Given** 用户已登录且处于聊天界面，**When** 用户上传一张图片并输入"描述这张图片"，**Then** 系统在 5 秒内开始返回图片的文字描述（首字节时间）
2. **Given** 用户已上传图片并收到回复，**When** 用户继续追问"图片中的物体有什么用途？"，**Then** 系统基于上下文理解并回答追问
3. **Given** 用户上传了一张包含文字的图片，**When** 用户要求"读出图片中的文字"，**Then** 系统准确识别并输出图片中的文字内容 (OCR)

---

### User Story 2 - 中途停止 AI 响应 (Priority: P1)

用户在 AI 正在生成响应时，可以随时点击"停止"按钮终止当前推理，AI 立即停止输出并等待用户下一条指令。这是实时交互的核心能力。

**Why this priority**: 终止推理是用户控制 AI 的基本能力，当 AI 回答偏离预期或用户改变想法时必须能够立即中断，避免浪费时间和计算资源。

**Independent Test**: 用户发送一条需要长回复的问题，在 AI 回答过程中点击停止按钮，AI 立即停止输出。

**Acceptance Scenarios**:

1. **Given** AI 正在流式输出响应，**When** 用户点击"停止生成"按钮，**Then** AI 在 500ms 内停止输出，已生成的内容保留显示
2. **Given** AI 已停止响应，**When** 用户发送新消息，**Then** 系统正常处理新请求，不受之前中断的影响
3. **Given** 用户正在进行语音对话，**When** 用户点击"打断"按钮，**Then** AI 立即停止当前语音输出，用户可继续录制新语音
4. **Given** 网关正在处理推理请求，**When** 收到取消指令，**Then** 网关通知模型服务终止推理，释放计算资源

**术语说明**（本 User Story 涉及三种不同的"停止"行为）：

| 术语 | 英文标识 | 触发方式 | 作用范围 |
|------|----------|----------|----------|
| **推理取消** | `cancel_inference` | 用户点击"停止生成"按钮 | 后端中断 SSE 流 + Gateway `/v1/chat/cancel` 终止模型推理 |
| **语音打断** | `interrupt_playback` | 用户点击"打断"按钮（语音对话模式） | 仅前端：停止 TTS 音频播放、清空播放队列，不联动后端 |
| **取消指令** | `cancel_request` | 推理取消的内部机制 | InferenceService → Redis Pub/Sub INFERENCE_CANCEL 事件 → AgentService 响应 |

---

### User Story 3 - 文档解析与问答 (Priority: P2)

用户上传 PDF 文档或网页截图，AI 能够解析文档内容并回答关于文档的问题，支持表格、图表等复杂内容的理解。

**Why this priority**: 文档处理是知识工作者的高频需求，扩展了图像理解的实用价值。

**Independent Test**: 用户上传一份包含表格的 PDF 截图，提问"表格第三行的数据是什么？"，系统准确返回数据。

**技术路径说明**：文档处理存在两条独立路径：
1. 截图/图片类：作为图片附件走多模态推理（minicpm-v 视觉理解）
2. PDF/DOCX 原始文档：通过 Gateway 异步三阶段解析（创建任务→轮询→获取结果），解析结果作为纯文本走默认文本模型问答

**用户视角流程**：用户上传文档 → 系统显示解析进度 → 解析完成后用户提问 → AI 基于文档内容回答。实现时序详见 plan.md 和 contracts/document-parse.yaml。

**文档解析模型选择**：Gateway 的 `/v1/documents/parse` 接口 `model` 参数必填（无默认值）。LinChat 后端通过 settings.py 配置选择文档解析模型（`LLM_GATEWAY_DOC_PARSE_MODEL`，默认 `minicpm-v`，可选 `qwen2.5-vl`/`minicpm-o`）。注意 minicpm-v 和 minicpm-o 共用 GPU，可能触发热切换（30-120s），参见 `docs/multimodal-api-guide.md` 第 8.4 节。

**Acceptance Scenarios**:

1. **Given** 用户上传了一份 PDF 文档的截图，**When** 用户询问"这份文档的主题是什么？"，**Then** 系统分析文档内容并给出摘要
2. **Given** 用户上传了包含表格的图片，**When** 用户询问表格中特定位置的数据，**Then** 系统准确定位并返回对应数据
3. **Given** 用户上传了流程图，**When** 用户要求解释流程，**Then** 系统描述流程图的步骤和逻辑
4. **Given** 用户上传了一份 PDF 原始文档（非截图），**When** 用户询问"总结这份文档的要点"，**Then** 系统通过 Gateway 文档解析服务提取文档内容，SSE 推送解析进度，解析完成后返回文档摘要
5. **Given** 文档解析过程中，**When** Gateway 返回解析失败（如文件损坏 E6003），**Then** 系统通过 SSE error 事件通知前端并显示友好错误提示

---

### User Story 4 - 视频内容分析 (Priority: P3)

用户上传短视频，AI 能够理解视频内容并回答关于视频的问题，如描述视频中发生了什么、识别视频中的关键事件等。

**Why this priority**: 视频理解是高级多模态能力，技术复杂度较高，但能显著提升产品竞争力。

**Independent Test**: 用户上传一段 10 秒的视频并提问"视频里发生了什么？"，系统返回视频内容描述。

**Acceptance Scenarios**:

1. **Given** 用户上传了一段短视频（≤60秒），**When** 用户询问"视频中发生了什么？"，**Then** 系统在 30 秒内返回视频内容描述
2. **Given** 用户上传了包含多个场景的视频，**When** 用户询问特定时间点的内容，**Then** 系统描述该时间点的画面
3. **Given** 视频处理中，**When** 从发送请求到收到首个 SSE content 块的时间超过视频时长的 2 倍（SC-005），**Then** 前端基于本地计时显示提示"AI 正在分析视频，请耐心等待..."（非后端推送，视频无独立后端预处理阶段，此为前端本地 timeout 检测提示）

---

### User Story 5 - 语音输入与识别 (Priority: P4)

用户通过语音输入与 AI 对话，系统自动将语音转换为文字，AI 理解语音内容并以文字或语音形式回复。

**Why this priority**: 语音交互提供更自然的人机交互方式，但需要全模态模型支持，实现复杂度较高。

**Independent Test**: 用户录制一段 5 秒的语音"今天天气怎么样？"，系统识别语音并返回文字回复。

**Acceptance Scenarios**:

1. **Given** 用户处于聊天界面，**When** 用户点击录音按钮并说话后发送，**Then** 系统将语音作为附件通过 minicpm-o 处理，返回 AI 文字回复（非实时 ASR，整段语音处理后响应）
2. **Given** 用户发送了语音消息，**When** AI 完成处理，**Then** AI 以文字形式回复用户问题
3. **Given** 用户录制语音后，**When** 语音消息准备发送，**Then** 输入框填入占位文本"[语音消息]"供用户编辑补充说明，语音文件作为附件一同发送。后端存储规则：content 字段原样存储用户最终提交的文本（可能是"[语音消息]"原文或用户编辑后的内容）；build_multimodal_messages() 构建模型输入时，**仅当消息同时携带 audio 类型附件时**，若 content 为"[语音消息]"则替换为空字符串（仅传音频附件给 minicpm-o），若用户有追加文字则保留作为文本上下文与音频一同发送；无 audio 附件时即使 content 恰好为"[语音消息]"也保留原文（防止用户文本误匹配）。（注：当前版本不做发送前的独立 ASR 预转写，语音理解由 minicpm-o 在推理阶段完成）

---

### User Story 6 - AI 语音回复 (Priority: P5)

AI 不仅以文字回复，还可以将回复内容转换为语音播放给用户，支持自然语音合成。

**Why this priority**: 语音回复是完整语音交互体验的补充，依赖于语音输入功能的实现。

**Independent Test**: 用户发送文字消息，系统返回文字回复，用户点击"播放语音"按钮听到回复内容。

**Acceptance Scenarios**:

1. **Given** AI 已返回文字回复，**When** 用户点击语音播放按钮，**Then** 系统以自然语音朗读回复内容
2. **Given** 用户开启了自动语音播放，**When** AI 返回回复，**Then** 系统自动播放语音回复 *【后续版本：当前版本仅支持手动点击播放，自动播放开关延后实现】*
3. **Given** 语音正在播放，**When** 用户点击暂停，**Then** 语音播放暂停

---

### Edge Cases

- 当用户上传的图片过大（>10MB）时，系统应提示用户压缩图片或拒绝处理
- 当用户上传的视频过长（>60秒）时，系统应告知处理限制
- 当上传的文件格式不支持时，系统应明确告知支持的格式
- 当语音录制时间过短（<1秒）或过长（>60秒）时，系统应给出提示
- 当网络中断时，依赖浏览器和前端框架的内置重试机制（如 fetch 失败提示），不实现断点续传
- 当用户快速连续点击停止按钮时，系统应正确处理防抖（500ms 防抖间隔，与推理取消 SLA 对齐）
- 当推理取消后用户立即发送新消息时，系统应能正常处理
- 当用户点击打断按钮时，应正确清理音频播放队列
- WebM 文件按 MIME type 区分媒体类型：video/webm 归类为视频，audio/webm 归类为音频
- 当用户上传的 PDF/DOCX 文档页数超过 200 页时（Gateway 侧限制），系统应通过 SSE error 事件返回 Gateway E6006 错误码对应的友好提示"文档页数超过限制（最大 200 页），请使用 pages 参数指定范围或上传更短文档"

## Requirements *(mandatory)*

### Functional Requirements

**网关接口规范**
- **FR-001**: 网关必须提供 OpenAI 兼容的多模态聊天接口（`/v1/chat/completions`）
- **FR-002**: 网关必须支持 `model` 参数指定多模态模型（如 `minicpm-v`、`minicpm-o`）
- **FR-003**: ~~网关必须在模型不可用时返回可用模型列表~~ *【Gateway 侧行为，非 LinChat 功能需求。Gateway 返回 E3002 时可能附带可用模型列表，LinChat 不解析该列表，仅展示"多模态服务暂时不可用，请稍后重试"友好错误提示，参见 T079】*
- **FR-004**: 网关接口必须支持流式响应（SSE）和非流式响应两种模式

**推理取消功能**
- **FR-005**: 系统必须提供推理取消接口（`/api/v1/chat/inference/cancel/`），支持终止正在进行的推理任务
- **FR-006**: 推理取消必须在 500ms 内生效，已生成的内容保留
- **FR-007**: 取消后必须正确释放模型计算资源，不影响后续请求
- **FR-008**: 前端必须提供明显的"停止生成"按钮，在 AI 响应期间可见
- **FR-009**: 语音对话模式下，用户点击"打断"按钮必须立即停止 AI 语音播放（半双工模式）

**图像处理**
- **FR-010**: 系统必须支持用户上传图片（JPG、PNG、GIF、WebP 格式）
- **FR-011**: 系统必须在收到图片后 5 秒内开始返回 AI 响应（首字节时间）
- **FR-012**: 系统必须支持单次消息中携带多个附件（最多 5 个，含图片/视频/音频/文档）。5 个限制为 MediaUploader 前端总选择数上限（含所有类型）；其中 document 类型走独立解析流程（不通过 chatRequest.attachments 参数），但仍计入总数限制。**前端分流规则**：用户点击发送时，前端按 media_type 拆分附件列表——image/video/audio 类型的 UUID 放入 `POST /api/v1/chat/` 请求的 attachments 字段；document 类型的 UUID 走 `POST /api/v1/chat/documents/parse/` 独立流程（解析完成后将结果文本与用户问题组合，作为纯文本消息发送）。同一条用户消息中 document 与 image/video/audio 不混合发送——如果附件中同时包含 document 和其他媒体类型，前端必须先完成文档解析流程获取 Markdown 结果文本，然后将解析结果拼入 content 字段（格式：`[文档内容]\n{markdown}\n[/文档内容]\n\n{user_question}`），与剩余 image/video/audio 附件一并通过单次 `POST /api/v1/chat/` 发送——不分两次发送，避免上下文割裂。**混合附件解析失败处理**：文档解析失败时（Gateway 返回 E6003/E6004/E6006 等错误），前端向用户展示解析失败原因，并提供两个选择：① 移除文档附件，仅发送剩余 image/video/audio 附件；② 取消整条消息发送，让用户修正文档后重试。默认不自动跳过失败文档——避免用户误以为 AI 已阅读文档内容
- **FR-013**: 系统必须支持图片中的文字识别（OCR）能力
- **FR-014**: 系统必须限制单张图片大小不超过 10MB
- **FR-014a**: 系统必须限制单个文档文件（PDF/DOCX）大小不超过 10MB（与 Gateway 侧限制对齐）

**视频处理**
- **FR-015**: 系统必须支持用户上传短视频（MP4、MOV、WebM 格式）
- **FR-016**: 系统必须在视频上传期间显示分阶段进度：上传阶段显示"上传中 X%"百分比进度（通过 HTTP 上传进度事件回调，如 XMLHttpRequest.upload.onprogress 或等效实现），上传完成后显示"准备就绪"状态图标。注：两个阶段均为前端本地状态，不涉及后端通知或 SSE 事件推送（视频无独立后端预处理阶段）
- **FR-017**: 系统必须限制视频时长不超过 60 秒，文件大小不超过 50MB

**语音处理**
- **FR-018**: 系统必须支持用户录制语音消息（WebM、WAV、MP3 格式，≤60秒，文件大小不超过 10MB）
- **FR-019**: 系统必须将语音转换为文字显示给用户。实现方式：语音作为音频附件通过 minicpm-o 模型在 `/v1/chat/completions` 接口中处理，模型接收音频后直接返回文字响应（无独立 ASR 端点）
- **FR-020**: 系统必须支持将 AI 回复转换为语音播放。当前版本 TTS 语音风格固定为 `default`（API 层 `voice` 参数预留扩展，见 contracts/tts.yaml），前端不暴露语音风格选择 UI
- **FR-020a**: AI 回复文本超过 2000 字符时，TTS 按钮置灰并提示"文本过长，暂不支持语音播放"。后续版本可实现分段合成
- **FR-021**: 语音对话支持半双工模式：用户手动点击"打断"按钮停止 AI 语音播放后再开始录音。全双工 VAD 自动打断延后至后续版本
- **FR-021a**: 语音消息在对话界面仅显示播放按钮，点击播放/暂停，简单化优先

**服务架构**
- **FR-022**: 多模态能力必须通过 LLM Gateway 统一提供
- **FR-023**: 多模态对话必须复用现有的对话历史和上下文机制
- ~~**FR-024**~~: [已移除] 参见宪法 9.2（单用户场景不实现并发控制）
- **FR-025**: 后端必须维护内容类型→模型ID的配置映射（如：音频→minicpm-o，图像/视频→minicpm-v），调用网关时明确指定 model 参数

**媒体存储与过期**
- **FR-026**: 后端不生成缩略图，仅存储原始媒体文件。前端内置静态 SVG 占位图资源（按媒体类型区分：图片/视频/音频/文档各一个），用于消息列表中的媒体预览展示和过期文件替代显示
- **FR-027**: 原始媒体文件保留7天后自动清理
- **FR-028**: 历史对话中文件未过期时，前端可通过媒体获取接口加载原始文件查看；文件过期后，显示对应媒体类型的静态占位图，保证体验一致性
- ~~**FR-029**~~: [已移除] 已合并至 FR-026 和 FR-028
- **FR-030**: 用户点击已过期媒体文件时，接口返回文件过期错误提示
- **FR-031**: 媒体文件仅上传者本人可访问，接口必须校验 user_id 所有权
- **FR-032**: 后端调用 LLM Gateway 的超时配置（参考 upstream-integration-guide.md §8.2）：推理请求 180 秒（多模态推理含图像编码，保守策略），文档解析创建 30 秒（含文件上传），文档解析结果获取 30 秒，取消请求 5 秒，轮询查询 30 秒，TTS 合成 60 秒（共 6 种）。超时后返回错误提示用户重试
- **FR-033**: 多模态推理必须复用现有 Langfuse 追踪机制，记录 trace/span 用于性能监控和成本分析
- **FR-034**: 前端将文档解析结果与用户问题组合发送时，若解析结果总字符数超过 8000 字符，截取前 8000 字符并追加截断提示文本 `\n\n[... 文档内容已截断，仅包含前 8000 字符 ...]`，截断阈值通过 `DOC_PARSE_MAX_RESULT_LENGTH` 配置（默认 8000）

### Gateway API Contract (与网关侧商定)

**权威参考**: [docs/upstream-integration-guide.md](../../docs/upstream-integration-guide.md) v2.0.0

**认证方式**: 所有 Gateway 接口调用必须携带 `Authorization: Bearer {LLM_GATEWAY_API_KEY}` 头。API Key 通过环境变量 `LLM_GATEWAY_API_KEY` 配置。

**请求追踪**: LinChat 后端在每次 Gateway 调用中传入 `X-Request-ID: {request_id}` 请求头，Gateway 会沿用此 ID 用于跨系统日志关联排查。

**错误响应格式映射**:
- Gateway 格式: `{"error": {"code": "Exxxx", "message": "...", "type": "...", "request_id": "...", "details": {}}}`
- LinChat 格式: `{"code": "...", "message": "...", "data": {...}}`
- 后端必须将 Gateway 错误映射为 LinChat 标准格式（`gateway_error` 码保留在 `data` 字段中用于调试）

**护栏参数**: LinChat 调用 Gateway 推理接口时，默认使用 `guardrails_level=fast`（延迟 < 10ms），不向用户暴露护栏控制。流式响应中若收到 `content_control` SSE 事件（安全护栏触发），丢弃已缓冲内容并使用 replacement 文本通过 SSE error 事件推送给前端。

**模型互斥约束**: minicpm-v 和 minicpm-o 共用 GPU 端口 8006，同一时间仅能运行其中一个。请求未加载模型时 Gateway 自动热切换（卸载当前→加载目标），切换期间请求等待。

**端点实现状态说明**:
- `/v1/chat/cancel`（推理取消）和 `/v1/audio/speech`（TTS）尚未在 upstream-integration-guide.md v2.0.0 中列出
- 若 Gateway 未实现 cancel 端点，LinChat 侧仅执行 Redis 清理（INFERENCE_CANCEL 事件→SSE 中断），模型侧推理自行完成
- 若 Gateway 未实现 TTS 端点，后端返回 503 TTS_SERVICE_UNAVAILABLE

**不实现自动模型降级**: 当 Gateway 返回模型不可用（E3002）时，LinChat 仅展示友好错误提示"多模态服务暂时不可用，请稍后重试"，不尝试自动切换到备选模型。

**Gateway 接口详细定义**：参见 [docs/upstream-integration-guide.md](../../docs/upstream-integration-guide.md) v2.0.0 和 [contracts/](./contracts/) 目录。涉及端点：`/v1/chat/completions`（多模态聊天）、`/v1/chat/cancel`（推理取消，待确认）、`/v1/models`（模型列表）、`/v1/documents/parse`（文档解析三步流程）、`/v1/audio/speech`（TTS，待确认）。错误码映射参见 upstream-integration-guide.md §6。

### Key Entities

- **MultimodalMessage**: 包含多种内容类型（文本、图片、视频、音频）的消息，扩展现有 Message 模型
- **MediaAttachment**: 用户上传的媒体文件元数据（类型、大小、MinIO 存储路径、过期状态、创建时间、过期时间）；media_type 支持 image/video/audio/document 四种类型；存储路径：`media/{user_id}/{YYYY-MM-DD}/{uuid}.{ext}`；原始文件保留7天后自动清理；前端通过内置静态 SVG 占位图（按媒体类型区分）展示媒体预览，文件过期后显示占位图
- **InferenceTask**: 推理任务状态（Redis 临时存储，非数据库实体）。复用 `EventService` 机制，使用 Redis 键 `user:{user_id}:inference_task` 存储 `{request_id, model, started_at, media_types}`，任务完成后自动清理。扩展 `EventType` 枚举添加 `INFERENCE_CANCEL` 事件类型
- **InterruptionEvent**（概念实体，非独立存储）: 通过 `EventType.INFERENCE_CANCEL` 和 Redis Pub/Sub 实现，记录打断类型（用户点击/语音打断）和时间戳

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 用户上传图片后，AI 首字节响应时间 < 5 秒 *【多模态推理涉及图像编码和视觉特征提取，首字节时间高于纯文本 TTFT（< 2s），5 秒为多模态场景合理阈值】*
- ~~**SC-002**~~: [已移除] 图片描述准确率由模型能力决定，LinChat 侧无法调优，不作为验收指标
- ~~**SC-003**~~: [已移除] OCR 准确率由模型能力决定，同上
- ~~**SC-004**~~: [已移除] 语音识别准确率由模型能力决定，同上
- **SC-005**: 视频处理完成时间 < 视频时长的 2 倍（测量区间：从前端发送 POST /api/v1/chat/ 请求起，到 SSE 收到首个 content 块止；即包含 Gateway 视频编码和模型推理首字节延迟）
- **SC-006**: 多模态服务可用性 > 99%（测量窗口：月度滚动 30 天；计算公式：成功推理数 / 总推理请求数 × 100%；排除条件：用户主动取消（interrupted）、Gateway 计划维护窗口；数据源：Langfuse trace 成功率统计）
- **SC-007**: 80% 的用户能在首次使用时成功完成图片上传和问答（"成功"定义：首次上传图片后收到非 error 类型的 AI 回复 SSE 事件） *【上线后通过 Langfuse trace 统计首次用户成功率，非上线前验收项】*
- **SC-008**: ~~独立指标已合并~~ → 参见 FR-006（推理取消 500ms SLA）
- **SC-009**: 中断后下一次请求成功率 > 99%

## Assumptions

1. LLM Gateway 已部署并提供标准化接口，支持多模态模型路由（已确认：upstream-integration-guide.md v2.0.0）
2. ~~网关侧同意实现推理取消接口（`/v1/chat/cancel`）~~ *【待确认：该端点未在 upstream-integration-guide.md v2.0.0 中列出，需与 Gateway 侧确认实现状态】*
3. MiniCPM 模型服务已在网关后端部署，提供 GPU 计算资源（minicpm-v 18GB / minicpm-o 21.6GB，参见 upstream-integration-guide.md §3.1）
4. 前端已具备文件上传组件的基础能力
5. 现有的对话历史机制可以扩展支持多模态内容
6. 用户网络环境能够支持上传较大文件（视频最大 50MB）
7. Gateway TTS 端点（`/v1/audio/speech`）可用 *【待确认：该端点未在 upstream-integration-guide.md v2.0.0 中列出】*

## Dependencies

- LLM Gateway 提供多模态模型接口
- LLM Gateway 实现推理取消能力
- 前端文件上传组件
- 媒体文件存储服务（MinIO 或类似）
- 现有聊天功能的消息模型

## Out of Scope

- 实时视频流分析
- 多人语音通话
- 视频生成功能
- 语音实时翻译
- 模型微调和训练
- 网关内部的模型部署和运维（由网关侧负责）

## Clarifications

### Session 2026-02-06

- Q: 用户上传的图片/视频文件保留多长时间？ → A: 7天自动清理 - 文件保留7天后自动删除，仅保留引用记录
- Q: 单用户同时进行多模态推理的最大并发数？ → A: 不做并发控制。本项目为家庭场景单用户系统，任何时刻仅一个用户在使用。如需中断当前推理，用户手动点击"停止"按钮后再发送新请求
- Q: 多模态请求时如何选择使用哪个模型？ → A: 按内容类型自动路由，LinChat 后端通过配置维护内容类型→模型ID映射，调用网关时明确指定 model 参数（有音频用 minicpm-o，纯图像/视频用 minicpm-v）
- Q: 视频上传和处理期间如何向用户展示进度？ → A: 分阶段进度 - 分别显示"上传中 X%"和"准备就绪"两个阶段（视频无独立后端预处理，上传完成即准备就绪）
- Q: 历史对话中已过期的媒体文件如何展示？ → A: 前端显示对应媒体类型的静态 SVG 占位图，保证体验一致；点击过期文件时接口返回文件过期错误
- Q: InferenceTask 如何存储？ → A: 使用 Redis 临时状态存储运行中的推理任务，任务完成后自动清理，不持久化到数据库；复用现有事件中控机制
- Q: 媒体文件在 MinIO 中的存储路径结构？ → A: 按用户+日期分层：`media/{user_id}/{YYYY-MM-DD}/{uuid}.{ext}`，便于按时间批量清理过期文件
- Q: 语音消息在对话中如何展示？ → A: 仅播放按钮，点击播放/暂停，简单化优先
- Q: 用户已有推理进行中时发送新多模态请求如何处理？ → A: 不做并发控制和弹窗提示。用户自行通过"停止"按钮取消当前推理后再发送新请求（宪法 9.2 单用户约束）
- Q: 媒体文件的访问权限控制策略？ → A: 仅上传者本人可访问，接口校验 user_id 所有权
- Q: 后端调用 LLM Gateway 的超时阈值？ → A: 分场景配置——推理 180 秒，文档解析创建 30 秒，文档解析结果 30 秒，取消 5 秒，轮询 30 秒，TTS 60 秒（共 6 种，参考 upstream-integration-guide.md §8.2）
- Q: 多模态推理的可观测性策略？ → A: 复用现有 Langfuse 追踪，记录 trace/span 用于性能监控和成本分析

### Session 2026-02-11（upstream-integration-guide.md v2.0.0 对齐）

- Q: Gateway 返回模型不可用（E3002）时是否自动降级到备选模型？ → A: 不实现自动模型降级，仅展示"多模态服务暂时不可用"友好错误，用户手动重试
- Q: Gateway `/v1/chat/cancel` 和 `/v1/audio/speech` 端点尚未在上游文档列出怎么办？ → A: 实现时做好降级处理——cancel 不可用时仅执行 Redis 侧清理（AgentService 停止消费 SSE 流），TTS 不可用时返回 503
- Q: Gateway 错误响应格式与 LinChat 不同怎么处理？ → A: 后端统一映射——Gateway `{"error":{"code":"Exxxx",...}}` → LinChat `{"code":"...","message":"...","data":{"gateway_error":"Exxxx",...}}`
- Q: Gateway 护栏系统如何对接？ → A: LinChat 默认使用 `guardrails_level=fast`（延迟 < 10ms），不向用户暴露护栏参数。流式中收到 `content_control` 事件时，丢弃已缓冲内容，使用 replacement 文本通过 SSE error 推送
- Q: 文档解析创建超时应设多少？ → A: 30 秒（含文件上传，对齐 upstream-integration-guide.md §8.2 建议）~~，非之前的 180 秒（历史对话上下文，已修正）~~
- Q: minicpm-v 和 minicpm-o 同时请求会怎样？ → A: 共用 GPU 端口 8006，Gateway 自动热切换（切换期间请求等待），LinChat 无需额外处理
