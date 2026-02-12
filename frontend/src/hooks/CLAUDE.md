# hooks 模块指南

## Hook 列表

| Hook | 职责 |
|------|------|
| `useChatStream.ts` | 聊天流式响应管理（发送、停止、恢复、重连、历史加载） |
| `useAudioRecorder.ts` | 语音录音（MediaRecorder API 封装、时长限制、格式输出） |
| `useDocParse.ts` | 文档解析状态管理（SSE 进度事件监听、结果获取） |

## useChatStream 状态机

```
idle → sending → generating → done/interrupted/error
                     ↓
              stop → interrupted
```

- `send()`: 乐观更新（临时消息）→ SSE 流 → 替换为真实消息
- `stop()`: 并行调用 stopGeneration + cancelInference
- Gateway 错误：检测 `data.gateway_error` 设置 `gatewayRetryAfter` 倒计时
