# Phase 0: Technical Research

**Feature**: 大模型聊天页面
**Date**: 2026-01-25

---

## 1. 验证码方案

### Decision: captcha 库

**Rationale**:
- Python 原生库，无外部服务依赖
- 支持自定义字体、颜色、干扰线
- 生成 Base64 图片，前端直接显示

**Alternatives Considered**:
| 方案 | 优点 | 缺点 | 结论 |
|------|------|------|------|
| captcha (本地生成) | 无外部依赖、可控 | 需要字体文件 | ✅ 选用 |
| reCAPTCHA | 安全性高 | 需要Google服务、国内访问受限 | ❌ 排除 |
| hCaptcha | 隐私友好 | 需要外部服务 | ❌ 排除 |
| 滑块验证 | 用户体验好 | 实现复杂 | ❌ 初期排除 |

**Implementation**:
```python
from captcha.image import ImageCaptcha

image = ImageCaptcha(width=120, height=40)
data = image.generate('ABCD')
base64_img = base64.b64encode(data.getvalue()).decode()
```

---

## 2. 国密算法库

### Decision: gmssl

**Rationale**:
- 中国官方国密算法Python实现
- 支持SM2(非对称)、SM3(哈希)、SM4(对称加密)
- 活跃维护，PyPI可安装

**Alternatives Considered**:
| 方案 | 优点 | 缺点 | 结论 |
|------|------|------|------|
| gmssl | 官方实现、功能完整 | - | ✅ 选用 |
| gmssl-python | 另一个实现 | 更新较少 | ❌ 排除 |
| pysmx | 轻量 | 功能不全 | ❌ 排除 |
| 自行实现 | 可控 | 工作量大、易出错 | ❌ 排除 |

**Implementation**:
```python
from gmssl import sm3, sm4

# SM3 哈希
def sm3_hash(data: str) -> str:
    return sm3.sm3_hash(list(data.encode()))

# SM4 加密
cipher = sm4.CryptSM4()
cipher.set_key(key, sm4.SM4_ENCRYPT)
encrypted = cipher.crypt_ecb(data)
```

---

## 3. LangGraph Checkpoint 方案

### Decision: langgraph-checkpoint-redis

**Rationale**:
- LangGraph 官方推荐方案
- 支持 TTL 自动过期
- 支持 refresh_on_read（读取时刷新TTL）
- 高性能，适合对话状态管理

**Alternatives Considered**:
| 方案 | 优点 | 缺点 | 结论 |
|------|------|------|------|
| langgraph-checkpoint-redis | 官方、高性能、TTL支持 | - | ✅ 选用 |
| langgraph-checkpoint-postgres | 持久化 | 配置复杂、性能较差 | ❌ 排除 |
| langgraph-checkpoint-sqlite | 简单 | 不支持TTL、不适合生产 | ❌ 排除 |
| MemorySaver | 开发简单 | 进程重启丢失 | ❌ 排除 |

**Implementation**:
```python
from langgraph.checkpoint.redis import RedisSaver

checkpointer = RedisSaver.from_conn_string(
    "redis://localhost:6379",
    ttl={"default_ttl": 60 * 24, "refresh_on_read": True}
)
checkpointer.setup()  # 创建索引

# 使用
agent = create_react_agent(model, tools, checkpointer=checkpointer)
config = {"configurable": {"thread_id": f"user_{user_id}"}}
```

---

## 4. 流式响应方案

### Decision: SSE (Server-Sent Events)

**Rationale**:
- 单向流式，适合AI响应场景
- 比WebSocket更简单，无需双向通信
- HTTP协议，无需额外端口
- Django支持 StreamingResponse

**Alternatives Considered**:
| 方案 | 优点 | 缺点 | 结论 |
|------|------|------|------|
| SSE | 简单、单向流式、HTTP原生 | 仅单向 | ✅ 选用 |
| WebSocket | 双向通信 | 配置复杂、需要Channel Layer | ❌ 初期排除 |
| 长轮询 | 兼容性好 | 延迟高、资源消耗大 | ❌ 排除 |
| gRPC Streaming | 高性能 | 需要额外协议 | ❌ 排除 |

**Implementation**:
```python
# 后端
from django.http import StreamingHttpResponse

async def chat_stream(request):
    async def event_generator():
        async for chunk in agent.astream_events(...):
            yield f"data: {json.dumps(chunk)}\n\n"
    return StreamingHttpResponse(event_generator(), content_type="text/event-stream")

# 前端
const response = await fetch('/api/chat', { method: 'POST', ... });
const reader = response.body.getReader();
while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    // 处理 chunk
}
```

---

## 5. Markdown 渲染方案

### Decision: react-markdown + rehype-highlight

**Rationale**:
- React 生态主流方案
- 支持 GitHub Flavored Markdown (GFM)
- 插件系统支持代码高亮、表格等
- 流式渲染友好

**Alternatives Considered**:
| 方案 | 优点 | 缺点 | 结论 |
|------|------|------|------|
| react-markdown | 成熟、插件丰富 | - | ✅ 选用 |
| marked + DOMPurify | 轻量 | 需要手动处理安全 | ❌ 排除 |
| markdown-it | 功能强大 | 非React原生 | ❌ 排除 |
| remark | 底层 | 需要更多配置 | ❌ 排除 |

**Implementation**:
```tsx
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import rehypeHighlight from 'rehype-highlight';

<ReactMarkdown
    remarkPlugins={[remarkGfm]}
    rehypePlugins={[rehypeHighlight]}
>
    {content}
</ReactMarkdown>
```

---

## 6. Mermaid 流程图渲染

### Decision: mermaid + useEffect

**Rationale**:
- 官方 Mermaid 库
- 流式传输完成后再渲染（避免中间状态错误）
- 支持多种图表类型

**Alternatives Considered**:
| 方案 | 优点 | 缺点 | 结论 |
|------|------|------|------|
| mermaid + useEffect | 官方、功能完整 | 需要等待完成 | ✅ 选用 |
| react-mermaid | React封装 | 依赖较旧 | ❌ 排除 |
| 服务端渲染SVG | 无客户端依赖 | 增加后端复杂度 | ❌ 排除 |

**Implementation**:
```tsx
import mermaid from 'mermaid';

useEffect(() => {
    if (!isLoading) {
        mermaid.init(undefined, '.mermaid');
    }
}, [content, isLoading]);

// 检测 Mermaid 代码块
const hasMermaid = content.includes('```mermaid');
```

---

## 7. 前端状态管理

### Decision: Zustand

**Rationale**:
- 轻量（2KB）
- 无 Provider 包裹
- TypeScript 友好
- 宪法 2.2 推荐

**Implementation**:
```typescript
import { create } from 'zustand';

interface ChatStore {
    messages: Message[];
    isLoading: boolean;
    addMessage: (msg: Message) => void;
    updateLastMessage: (content: string) => void;
}

export const useChatStore = create<ChatStore>((set) => ({
    messages: [],
    isLoading: false,
    addMessage: (msg) => set((state) => ({ messages: [...state.messages, msg] })),
    updateLastMessage: (content) => set((state) => ({
        messages: state.messages.map((msg, i) =>
            i === state.messages.length - 1 ? { ...msg, content: msg.content + content } : msg
        )
    })),
}));
```

---

## 8. Token 存储方案

### Decision: httpOnly Cookie

**Rationale**:
- 宪法 4.1 强制要求
- 防止 XSS 攻击读取 Token
- 后端设置，前端无需手动管理

**Implementation**:
```python
# 后端设置 Cookie
response.set_cookie(
    key="access_token",
    value=token,
    httponly=True,
    secure=True,  # 生产环境 HTTPS
    samesite="Lax",
    max_age=3600
)

# 前端请求自动携带
fetch('/api/chat', { credentials: 'include' });
```

---

## Research Summary

| 主题 | 决策 | 依赖包 |
|------|------|--------|
| 验证码 | captcha | `captcha` |
| 国密算法 | gmssl | `gmssl` |
| LangGraph Checkpoint | Redis | `langgraph-checkpoint-redis` |
| 流式响应 | SSE | Django StreamingHttpResponse |
| Markdown渲染 | react-markdown | `react-markdown`, `remark-gfm`, `rehype-highlight` |
| Mermaid渲染 | mermaid | `mermaid` |
| 状态管理 | Zustand | `zustand` |
| Token存储 | httpOnly Cookie | - |

---

## Dependencies Summary

### Backend (Python)
```txt
django>=4.2
djangorestframework>=3.14
langgraph>=0.2
langgraph-checkpoint-redis>=0.1
langchain-openai>=0.1
langfuse>=2.0
gmssl>=3.2
captcha>=0.5
redis>=5.0
psycopg2-binary>=2.9
```

### Frontend (Node.js)
```json
{
  "dependencies": {
    "next": "^14.0",
    "react": "^18.0",
    "zustand": "^4.5",
    "react-markdown": "^9.0",
    "remark-gfm": "^4.0",
    "rehype-highlight": "^7.0",
    "mermaid": "^10.0"
  }
}
```
