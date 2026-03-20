# app/chat 模块指南

## 模块概述

聊天页面，LinChat 的核心交互页面。组合 `useChatStream` Hook 和聊天组件实现完整的聊天功能。

## 文件清单

| 文件 | 用途 |
|------|------|
| `page.tsx` | 聊天页面主组件（ChatPage） |

## 页面结构

```
ChatPage (flex h-screen)
├── 主内容区 (flex-1)
│   ├── Header (顶部导航栏)
│   │   ├── Logo + 标题 "LinChat"
│   │   ├── 用户名显示
│   │   ├── 模型配置按钮 (仅管理员可见)
│   │   ├── 监控面板切换按钮 (MonitorToggleButton)
│   │   └── 退出按钮
│   ├── NetworkError (错误横幅)
│   ├── MessageList (消息列表)
│   ├── MessageInput (输入框)
│   ├── VoiceModeContainer (语音控制面板, 动态导入)
│   └── ContextStatusBar (上下文状态条, 有监控数据时显示)
└── MonitorSidebar (监控侧边栏, 可折叠)
```

## 关键逻辑

### 状态来源

- `useChatStream()`: messages、isGenerating、isCompacting、error、failedContent 等聊天状态
- `useContextMonitor()`: monitorData、tokenHistory、contextHistory 监控数据
- `useAuth()`: user 用户信息、logout 登出方法
- `useState`: monitorOpen 侧边栏开关状态（默认开启）
- `voiceStore`: voiceMode 语音模式开关
- `memberStore`: targetUserId/targetUsername/isViewingOther 成员切换状态（015-family-multiuser）

### 错误处理与重试

- `NetworkError` 组件显示错误横幅
- 有 `failedContent` 时显示重试按钮
- 重试逻辑: 取出 `failedContent` + `failedAttachments` -> `clearFailedContent()` -> `send(content, attachments)`
- Gateway 倒计时: `gatewayRetryAfter` 传递给 `NetworkError`，倒计时结束后通过 `useChatStore.getState().setGatewayRetryAfter(0)` 重置

### 权限控制

- 模型配置按钮仅 `user.type === 'admin'` 时可见
- 认证依赖 `useAuth()` Hook

### 语音模式

- `VoiceModeContainer` 通过 `next/dynamic` 动态导入，减少非语音用户的包体积
- 底部工具栏包含语音模式切换按钮（麦克风图标）
- 点击按钮调用 `voiceStore.setVoiceMode(!voiceModeActive)` 切换语音模式
- 进入语音模式时弹出底部语音控制面板

### 页面导航

- 模型配置: `router.push('/settings')`
- 登出: `logout()` -> `router.push('/login')`

## 数据流

```
ChatPage
  ├── useChatStream → chatService (SSE/REST) → 后端
  ├── useContextMonitor → window.CustomEvent('context_status') → SSE 事件
  ├── useAuth → authService → 后端
  └── UI 组件
       ├── MessageList (props: messages, isGenerating, isCompacting, ...)
       ├── MessageInput (props: isGenerating, failedContent, onSend, onStop, ...)
       ├── NetworkError (props: error, gatewayRetryAfter, onRetry, ...)
       ├── VoiceModeContainer (动态导入)
       │     └── useVoiceMode → WebSocket → 后端 VoiceConsumer
       ├── MonitorSidebar (props: isOpen, data, tokenHistory, contextHistory)
       └── ContextStatusBar (props: pct, alert)
```

## 依赖关系

- `useChatStream` Hook（核心聊天逻辑）
- `useContextMonitor` Hook（监控数据）
- `useAuth` Hook（认证）
- `chatStore`（直接访问 `getState()` 重置 gatewayRetryAfter）
- 组件: `MessageList`、`MessageInput`、`NetworkError`、`MonitorSidebar`、`ContextStatusBar`、`MonitorToggleButton`
- `voiceStore`（语音模式开关）
- 组件: `VoiceModeContainer`

## 测试方法

- 页面集成测试: mock hooks 和服务，验证消息发送/停止/重试流程
- 权限测试: 验证管理员/普通用户按钮可见性
- 错误处理测试: 验证 NetworkError 显示/重试/倒计时
