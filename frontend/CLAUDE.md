# Frontend 开发指南

> **单用户单会话原则**: 一个用户永远对应一个会话，Message 中没有 conversation_id，只有 user_id。不存在多会话、不考虑并发会话。

## 项目结构

```
frontend/
├── src/
│   ├── app/                # Next.js App Router 页面
│   │   ├── chat/           # 聊天主页面（ChatPage）
│   │   └── settings/       # 设置页面（模型配置/记忆管理/语音设置/设备管理）
│   ├── components/
│   │   ├── chat/           # 聊天组件（MessageList/MessageInput/AudioPlayer/MonitorPanel 等）
│   │   ├── voice/          # 语音组件（VoiceModePanel/VoiceWaveform/VoiceMessageBubble/VoiceModeContainer）
│   │   └── settings/       # 设置组件
│   ├── hooks/              # React Hooks（useChatStream/useVoiceMode/useVoiceWebSocket/usePCMAudioCapture 等）
│   ├── stores/             # Zustand Store（chatStore/uploadStore/modelStore/voiceStore）
│   ├── services/           # API 服务层（chatService/mediaApi/voiceApi/modelService）
│   ├── types/              # TypeScript 类型定义（index/media/model/voice/sm-crypto.d）
│   └── utils/              # 工具函数（crypto SM4 加密）
├── public/                 # 静态资源
├── next.config.mjs         # Next.js 配置（basePath=/linchat）
└── tailwind.config.ts      # Tailwind CSS 配置
```

## 技术栈

| 类别 | 技术 |
|------|------|
| 框架 | Next.js 14+ / React 18+ / TypeScript 5.0+ |
| 样式 | Tailwind CSS |
| 状态 | Zustand（chatStore/uploadStore/modelStore/voiceStore） |
| 加密 | sm-crypto（SM4 对称加密） |
| 音频 | Web AudioWorklet API（PCM16 采集） |
| 通信 | SSE（聊天流式）+ WebSocket（语音） |

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


<claude-mem-context>

</claude-mem-context>
