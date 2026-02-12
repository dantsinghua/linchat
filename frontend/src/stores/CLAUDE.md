# stores 模块指南

## Store 列表

| Store | 职责 |
|-------|------|
| `chatStore.ts` | 聊天状态（messages、isGenerating、error、gatewayRetryAfter） |
| `uploadStore.ts` | 媒体上传状态（pendingFiles、uploadProgress、attachments） |
| `modelStore.ts` | 模型配置状态 |

## 状态管理规范

- 使用 Zustand（非 Redux）
- Store 通过 `create<State>()` 定义
- 组件通过 `useXxxStore()` 消费状态
- 外部访问（非 React 上下文）使用 `useXxxStore.getState()`
