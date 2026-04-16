# Frontend 开发指南

> **单用户单会话原则**: 一个用户永远对应一个会话，Message 中没有 conversation_id，只有 user_id。不存在多会话、不考虑并发会话。

## 项目结构

```
frontend/
├── src/
│   ├── app/                # Next.js App Router 页面
│   │   ├── chat/           # 聊天主页面（ChatPage）
│   │   ├── settings/       # 设置页面（模型配置/记忆管理/语音设置/设备管理）
│   │   ├── login/          # 登录页面
│   │   └── 401/            # 未授权页面
│   ├── components/
│   │   ├── auth/           # 认证组件（CaptchaImage, LoginForm）
│   │   ├── chat/           # 聊天组件（MessageList/MessageInput/NetworkError/MediaUploader/MediaPreview/AudioPlayer/AudioRecorder/ContextMonitorPanel/MarkdownRenderer/MermaidRenderer）
│   │   ├── voice/          # 语音组件（VoiceModePanel/VoiceWaveform/VoiceMessageBubble/VoiceModeContainer）
│   │   ├── settings/       # 设置组件（ModelConfigCard/Form, VoiceSettingsCard, SpeakerProfileCard, DeviceManageCard）
│   │   └── ui/             # UI 基础组件库
│   ├── hooks/              # React Hooks（useChatStream/useVoiceMode/useVoiceWebSocket/usePCMAudioCapture/useAudioRecorder/useDocParse/useVoiceErrorHandler/useAuth）
│   ├── stores/             # Zustand Store（chatStore/uploadStore/modelStore/voiceStore/memberStore）
│   ├── services/           # API 服务层（api/authService/authGuard/chatService/modelService/mediaApi/voiceApi/memberService）
│   ├── types/              # TypeScript 类型定义（index/media/model/voice/sm-crypto.d）
│   └── utils/              # 工具函数（crypto SM4 加密）
├── public/                 # 静态资源
├── next.config.mjs         # Next.js 配置（basePath=/linchat, output=standalone）
└── tailwind.config.ts      # Tailwind CSS 配置
```

## 技术栈

| 类别 | 技术 |
|------|------|
| 框架 | Next.js 14+ / React 18+ / TypeScript 5.0+ |
| 样式 | Tailwind CSS |
| 状态 | Zustand（chatStore/uploadStore/modelStore/voiceStore/memberStore） |
| HTTP | Axios（Cookie 认证、401/429 拦截器） |
| 加密 | sm-crypto（SM4 对称加密） |
| 音频 | Web AudioWorklet API（PCM16 采集, 16kHz/单声道/30ms 帧） |
| 渲染 | react-markdown（GFM）+ Mermaid（图表） |
| 通信 | SSE（聊天流式）+ WebSocket（语音双向） |
| 测试 | Jest + Playwright |

## 页面清单

| 页面 | 路径 | 用途 |
|------|------|------|
| Chat | `/chat` | 主聊天页面 + 语音模式 + 监控面板 |
| Settings | `/settings` | 模型配置（管理员）+ 语音设置 + 声纹 + 设备 |
| Login | `/login` | 用户登录（验证码） |
| 401 | `/401` | 未授权错误页 |
| / | `/` | 首页重定向到 `/chat` |

## Hooks 清单

| Hook | 行数 | 用途 |
|------|------|------|
| `useChatStream` | ~400 | 核心聊天状态机（发送/停止/恢复/循环重连 reconnectWithRetry/历史加载（500ms 防抖+滚动位置恢复）/乐观更新/失败恢复） |
| `useVoiceMode` | ~483 | 语音模式总控（8 态状态机: idle→configuring→listening→recording→processing→responding→interrupted→error） |
| `useVoiceWebSocket` | ~424 | WebSocket 连接管理（16 种下行事件映射、心跳 30s、自动重连 1 次） |
| `usePCMAudioCapture` | ~307 | AudioWorklet PCM16 采集（16kHz/单声道/每帧 480 samples = 960 bytes） |
| `useDocParse` | ~200 | 文档解析生命周期（SSE 进度事件监听、结果获取、最大 8000 字符截断） |
| `useAudioRecorder` | ~150 | MediaRecorder API 封装（时长限制 1-60s，输出 audio/webm） |
| `useVoiceErrorHandler` | ~128 | 语音异常状态（麦克风权限、页面可见性、网络状态） |
| `useAuth` | ~150 | 认证 Hook（登录/登出/当前用户） |

## Stores 清单

| Store | 核心状态 | 说明 |
|-------|---------|------|
| `chatStore` | messages, isGenerating, currentRequestId, failedContent, docParseProgress | 聊天全局状态 |
| `uploadStore` | tasks[], completedAttachments[] | 媒体上传任务管理 |
| `modelStore` | models[], isLoading | 模型配置列表 |
| `voiceStore` | voiceMode, sessionState, isRecording, settings, hasSpeakerProfile | 语音交互状态 |
| `memberStore` | targetUserId, targetUsername, members[], authUserId | 成员管理/代查模式（015） |

## Services 清单

| 服务 | 说明 |
|------|------|
| `api.ts` | Axios 实例（baseURL, Cookie 认证, 401/429 拦截器） |
| `authService.ts` | 登录/登出/验证码/当前用户 |
| `authGuard.ts` | 401 幂等重定向守卫 |
| `chatService.ts` | 消息发送（SSE 流）/停止/恢复/重连/历史加载 |
| `modelService.ts` | 模型配置 CRUD |
| `mediaApi.ts` | 媒体上传（XHR 进度）/下载/推理取消/文档解析 |
| `voiceApi.ts` | 声纹/设备/语音设置 CRUD（内置 snake_case/camelCase 转换） |
| `memberService.ts` | 成员列表/创建/声纹注册 API（015-family-multiuser） |

## 关键类型

### types/voice.ts（含 014-jarvis 新增）

- **VoiceSessionState**: 8 态状态机（idle/configuring/listening/recording/processing/responding/interrupted/error）
- **WebSocket 事件**: 16 种下行事件，014 新增 5 种（aggregation.utterance_added/completed, decision.result, tts.started/completed）
- **VoiceDecisionResult**: `{ decision: 'RESPOND' | 'RECORD_ONLY' | 'STOP', confidence? }`

### types/media.ts

- **MEDIA_LIMITS**: 图片 10MB / 视频 50MB / 音频 10MB / 时长 60s / 附件数 5

## 常用命令

```bash
cd /home/dantsinghua/work/linchat/frontend

# ⚠️ 必须先 build 再 start（禁止 npm run dev）
npm run build
npm run start -- -p 3784

# 代码检查
npm run lint
npm test
```

## 架构约束

1. **basePath**: 所有路由前缀 `/linchat`
2. **SSE 流式**: 聊天消息通过 SSE 接收（useChatStream）
3. **WebSocket**: 语音通过 WebSocket 双向通信（useVoiceWebSocket）
4. **状态管理**: Zustand，禁止 Redux，组件外用 `getState()`
5. **API 安全**: httpOnly Cookie 认证，密码/API Key 用 SM4 加密
6. **动态导入**: VoiceModeContainer 通过 `next/dynamic` 懒加载，减少非语音用户包体积
7. **乐观更新**: send() 先插入临时消息，SSE 返回后替换真实 ID


<claude-mem-context>

</claude-mem-context>