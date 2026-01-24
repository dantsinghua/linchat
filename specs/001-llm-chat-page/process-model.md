# 流程模型定义

本文档定义大模型聊天平台的业务流程。基于spec.md设计。

---

## 一、用户登录流程（P_AUTH_001）

```伪代码
@流程(P_AUTH_001, "用户登录流程")
  @对应用户故事: User Story 1
  @触发条件: 用户访问聊天页面且未登录
```

### 流程图

```
用户                    前端                      后端                     Redis
 │                       │                        │                        │
 │  1.访问聊天页         │                        │                        │
 │──────────────────────>│                        │                        │
 │                       │  2.检查本地Token        │                        │
 │                       │  无Token或无效          │                        │
 │  3.跳转登录页         │                        │                        │
 │<──────────────────────│                        │                        │
 │                       │                        │                        │
 │                       │  4.GET /captcha        │                        │
 │                       │───────────────────────>│                        │
 │                       │                        │  5.生成验证码           │
 │                       │                        │───────────────────────>│ SET 2min
 │                       │  6.返回captcha         │<───────────────────────│
 │                       │<───────────────────────│                        │
 │  7.显示验证码         │                        │                        │
 │<──────────────────────│                        │                        │
 │                       │                        │                        │
 │  8.填写表单提交       │                        │                        │
 │──────────────────────>│  9.SM4加密密码         │                        │
 │                       │───────────────────────>│                        │
 │                       │                        │  10.校验验证码          │
 │                       │                        │───────────────────────>│ GET+DEL
 │                       │                        │  11.检查锁定            │
 │                       │                        │  12.验证密码            │
 │                       │                        │  13.生成Token           │
 │                       │                        │───────────────────────>│ SET 1hour
 │                       │  14.返回Token          │                        │
 │                       │<───────────────────────│                        │
 │  15.跳转聊天界面      │                        │                        │
 │<──────────────────────│                        │                        │
```

### 异常处理

| 场景 | 异常 | 前端处理 |
|-----|------|---------|
| 验证码错误/过期 | CaptchaInvalidException | 提示错误，刷新验证码 |
| 账户已锁定 | AccountLockedException | 显示剩余锁定时间 |
| 用户名或密码错误 | AuthFailedException | 提示错误，刷新验证码 |

---

## 二、Token鉴权流程（P_AUTH_002）

```伪代码
@流程(P_AUTH_002, "Token鉴权流程")
  @对应用户故事: User Story 1 (场景5,6)
  @触发条件: 每次API请求
```

### 流程图

```
前端                    中间件                    Redis
 │                       │                        │
 │  API请求              │                        │
 │  Authorization:Bearer │                        │
 │──────────────────────>│                        │
 │                       │  1.提取Token           │
 │                       │  2.计算Hash            │
 │                       │  3.查询Redis           │
 │                       │───────────────────────>│
 │                       │  4.返回Token信息       │
 │                       │<───────────────────────│
 │                       │  5.刷新TTL为1小时       │
 │                       │───────────────────────>│ EXPIRE 3600
 │                       │  6.放行请求            │
 │  响应结果             │                        │
 │<──────────────────────│                        │
```

### 401响应时前端处理

```typescript
// React + Next.js 风格，Token 存储在 httpOnly Cookie 中（由后端设置/清除）
// 前端无需手动管理 Token，401 时直接跳转登录页
axios.interceptors.response.use(null, error => {
    if (error.response?.status === 401) {
        // Token 由 httpOnly Cookie 管理，前端无法直接操作
        // 后端会在登出时清除 Cookie
        window.location.href = '/login?redirect=' + encodeURIComponent(window.location.pathname);
    }
    return Promise.reject(error);
});
```

---

## 三、消息发送与流式响应流程（P_CHAT_001）

```伪代码
@流程(P_CHAT_001, "消息发送与流式响应流程")
  @对应用户故事: User Story 2
  @触发条件: 用户发送消息
  @关键技术: Redis Checkpoint自动管理对话历史
```

### 流程图

```
用户        前端              后端API           Redis             LangGraph        vLLM
 │           │                  │                │                  │              │
 │ 1.输入    │                  │                │                  │              │
 │──────────>│                  │                │                  │              │
 │           │ 2.校验消息       │                │                  │              │
 │           │ 3.显示用户消息   │                │                  │              │
 │           │                  │                │                  │              │
 │           │ 4.POST /chat     │                │                  │              │
 │           │─────────────────>│                │                  │              │
 │           │                  │                │                  │              │
 │           │                  │ 5.创建执行记录 │                  │              │
 │           │                  │    (MySQL)     │                  │              │
 │           │                  │                │                  │              │
 │           │                  │ 6.调用Agent(thread_id)            │              │
 │           │                  │───────────────────────────────────>│              │
 │           │                  │                │                  │              │
 │           │                  │                │ 7.RedisSaver     │              │
 │           │                  │                │   加载Checkpoint │              │
 │           │                  │                │<─────────────────│              │
 │           │                  │                │  (自动获取历史)   │              │
 │           │                  │                │                  │              │
 │           │                  │                │                  │ 8.调用LLM    │
 │           │                  │                │                  │─────────────>│
 │           │                  │                │                  │ 9.流式返回   │
 │           │                  │                │                  │<─────────────│
 │           │                  │                │                  │              │
 │           │                  │ 10.SSE chunk   │                  │              │
 │           │<─────────────────│<───────────────────────────────────│              │
 │ 11.逐字   │                  │   (多次循环)   │                  │              │
 │    显示   │                  │                │                  │              │
 │<──────────│                  │                │                  │              │
 │           │                  │                │                  │              │
 │           │                  │                │ 12.RedisSaver    │              │
 │           │                  │                │   保存Checkpoint │              │
 │           │                  │                │<─────────────────│              │
 │           │                  │                │  (自动保存状态)   │              │
 │           │                  │                │                  │              │
 │           │                  │ 13.保存消息到MySQL（双写）         │              │
 │           │                  │                │                  │              │
 │           │                  │ 14.更新执行记录│                  │              │
 │           │ 15.完成标记      │                │                  │              │
 │           │<─────────────────│                │                  │              │
 │ 16.完成   │                  │                │                  │              │
 │<──────────│                  │                │                  │              │
```

### 与传统方式对比

| 步骤 | 传统方式 | Redis Checkpoint方式 |
|-----|---------|---------------------|
| 加载历史 | 从MySQL查询最近N条消息 | **自动**：RedisSaver加载thread状态 |
| 构建上下文 | 手动拼接messages数组 | **自动**：LangGraph处理 |
| 保存状态 | 手动保存到MySQL | **自动**：RedisSaver保存checkpoint |
| 持久化 | 仅MySQL | MySQL双写（用于审计和历史查询） |

### 核心代码

**应用启动时初始化RedisSaver：**
```python
from contextlib import asynccontextmanager
from langgraph.checkpoint.redis import RedisSaver

# 全局checkpointer
redis_saver: RedisSaver = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global redis_saver
    # 启动时初始化（24小时TTL，与data-model.md一致）
    redis_saver = RedisSaver.from_conn_string(
        settings.REDIS_URL,
        ttl={"default_ttl": 60 * 24, "refresh_on_read": True}  # 24小时过期
    )
    redis_saver.setup()  # 创建索引
    yield
    # 关闭时清理
    # redis_saver会自动管理连接

app = FastAPI(lifespan=lifespan)
```

**后端SSE端点：**
```python
@router.post("/chat")
async def chat(request: Request, body: ChatRequest):
    user_id = request.state.user_id
    
    async def event_generator():
        async for chunk in send_message(user_id=user_id, content=body.content):
            yield f"data: {json.dumps(chunk.__dict__)}\n\n"
    
    return StreamingResponse(event_generator(), media_type="text/event-stream")
```

**Agent执行（使用Redis Checkpoint）：**
```python
async def execute_agent(user_id: int, content: str):
    thread_id = f"user_{user_id}"
    
    # 创建Agent时传入checkpointer
    agent = create_react_agent(
        model=model,
        tools=tools,
        checkpointer=redis_saver  # Redis自动管理历史
    )
    
    # 配置thread_id
    config = {"configurable": {"thread_id": thread_id}}
    
    # 只需传当前消息，历史由Checkpoint自动注入
    async for event in agent.astream_events(
        {"messages": [HumanMessage(content=content)]},
        config=config,
        version="v2"
    ):
        if event["event"] == "on_chat_model_stream":
            chunk = event["data"]["chunk"]
            if chunk.content:
                yield StreamChunk(type="content", content=chunk.content)
    
    yield StreamChunk(type="done", content="")
```

**前端SSE处理（React + Next.js 风格）：**
```typescript
// React Hook 风格，Token 通过 httpOnly Cookie 自动携带
const useChatStream = () => {
    const [messages, setMessages] = useState<Message[]>([]);

    const sendMessage = async (content: string) => {
        // 显示用户消息
        setMessages(prev => [...prev, { role: 'user', content }]);

        // 创建AI消息占位
        const aiMessageId = Date.now();
        setMessages(prev => [...prev, {
            id: aiMessageId,
            role: 'assistant',
            content: '',
            loading: true
        }]);

        // SSE流式接收（httpOnly Cookie 自动携带，无需手动设置 Authorization）
        const response = await fetch('/api/chat', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'include',  // 携带 httpOnly Cookie
            body: JSON.stringify({ content })
        });

        const reader = response.body!.getReader();
        const decoder = new TextDecoder();

        while (true) {
            const { done, value } = await reader.read();
            if (done) break;

            const text = decoder.decode(value);
            const lines = text.split('\n').filter(line => line.startsWith('data: '));

            for (const line of lines) {
                const data = JSON.parse(line.slice(6));
                if (data.type === 'content') {
                    setMessages(prev => prev.map(msg =>
                        msg.id === aiMessageId
                            ? { ...msg, content: msg.content + data.content }
                            : msg
                    ));
                } else if (data.type === 'done') {
                    setMessages(prev => prev.map(msg =>
                        msg.id === aiMessageId
                            ? { ...msg, loading: false }
                            : msg
                    ));
                }
            }
        }
    };

    return { messages, sendMessage };
};
```

---

## 四、历史消息加载流程（P_CHAT_002）

```伪代码
@流程(P_CHAT_002, "历史消息加载流程")
  @对应用户故事: User Story 2 (场景5,6,8)
  @触发条件: 用户进入聊天页面
```

### 流程图

```
用户                    前端                      后端
 │                       │                        │
 │  1.进入聊天页         │                        │
 │──────────────────────>│                        │
 │                       │  2.GET /messages       │
 │                       │───────────────────────>│
 │                       │                        │  3.查询user_id的消息
 │                       │                        │    (数据隔离)
 │                       │  4.返回消息列表        │
 │                       │<───────────────────────│
 │                       │  5.渲染消息            │
 │  6.显示历史消息       │  6.渲染Markdown        │
 │<──────────────────────│  6.渲染Mermaid         │
 │                       │                        │
 │  7.滚动到顶部         │                        │
 │──────────────────────>│                        │
 │                       │  8.GET /messages       │
 │                       │    ?before_seq=xxx     │
 │                       │───────────────────────>│
 │                       │  9.返回更早消息        │
 │                       │<───────────────────────│
 │  10.显示更多消息      │                        │
 │<──────────────────────│                        │
```

### 核心代码

```python
@router.get("/messages")
async def get_messages(
    request: Request,
    limit: int = 50,
    before_sequence: int = None
):
    user_id = request.state.user_id  # 从认证上下文获取，确保数据隔离
    
    messages = await load_history_messages(
        user_id=user_id,
        limit=limit,
        before_sequence=before_sequence
    )
    
    return messages
```

---

## 五、流程与验收场景映射

```
┌─────────────────────────────────────────────────────────────────────┐
│                     User Story 1 - 用户登录认证                      │
├─────────────────────────────────────────────────────────────────────┤
│ 场景1: 未登录自动跳转      →  P_AUTH_001                            │
│ 场景2: 正确凭证登录成功    →  P_AUTH_001                            │
│ 场景3: 验证码错误          →  P_AUTH_001 (异常处理)                 │
│ 场景4: 用户名或密码错误    →  P_AUTH_001 (异常处理)                 │
│ 场景5: Token有效期内操作   →  P_AUTH_002 (刷新TTL)                  │
│ 场景6: 1小时无操作过期     →  P_AUTH_002 (401处理)                  │
└─────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│              User Story 2 - 发送消息并获取AI流式响应                 │
├─────────────────────────────────────────────────────────────────────┤
│ 场景1: 消息发送后接收流式响应  →  P_CHAT_001                        │
│ 场景2: 响应逐步显示(打字效果)  →  P_CHAT_001 (前端SSE)              │
│ 场景3: Markdown实时渲染       →  P_CHAT_001 (前端渲染)              │
│ 场景4: Mermaid实时渲染        →  P_CHAT_001 (前端渲染)              │
│ 场景5: 刷新后历史消息正确显示  →  P_CHAT_002                        │
└─────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│                   User Story 3 - 系统配置管理                        │
├─────────────────────────────────────────────────────────────────────┤
│ 场景1: 系统启动读取数据库配置       →  配置服务                      │
│ 场景2: 系统启动读取LLM接口配置      →  配置服务                      │
│ 场景3: 系统启动读取Redis缓存配置    →  配置服务                      │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 六、流程索引

| 流程编码 | 流程名称 | 对应用户故事 | 涉及行为 |
|---------|---------|-------------|---------|
| P_AUTH_001 | 用户登录流程 | US1 | B_AUTH_001, B_AUTH_002 |
| P_AUTH_002 | Token鉴权流程 | US1(场景5,6) | B_AUTH_003 |
| P_CHAT_001 | 消息发送与流式响应流程 | US2 | B_CHAT_001, B_CHAT_002 |
| P_CHAT_002 | 历史消息加载流程 | US2(场景5,6,8) | B_CHAT_003 |

---

## 七、关键设计决策

1. **无conversation表**：单用户单会话场景下冗余，message直接关联user
2. **thread_id派生**：`f"user_{user_id}"`，无需额外查询
3. **Redis Checkpoint**：LangGraph对话状态由RedisSaver自动管理
4. **双写策略**：Checkpoint管理运行时状态，MySQL持久化聊天记录
5. **数据隔离**：所有查询通过user_id过滤，user_id从认证上下文获取
6. **SSE流式响应**：实现打字机效果，支持Markdown/Mermaid实时渲染

---

## 八、Checkpoint故障恢复策略

当Redis Checkpoint数据丢失时（如Redis重启、TTL过期），系统可从MySQL恢复：

```python
async def rebuild_checkpoint_if_needed(user_id: int, checkpointer: RedisSaver):
    """如果Checkpoint不存在，从MySQL重建"""
    thread_id = f"user_{user_id}"
    config = {"configurable": {"thread_id": thread_id}}
    
    # 检查Checkpoint是否存在
    existing = checkpointer.get(config)
    if existing:
        return  # 已存在，无需重建
    
    # 从MySQL加载历史消息
    messages = await message_repo.find_by_user_id(user_id, limit=20)
    if not messages:
        return  # 无历史，无需重建
    
    # 构建初始状态并保存
    initial_state = {
        "messages": [
            HumanMessage(content=m.content) if m.role == "user" 
            else AIMessage(content=m.content)
            for m in messages
        ]
    }
    
    # 通过运行一次空调用来初始化Checkpoint
    # 或者直接使用checkpointer.put()保存初始状态
    logger.info(f"Rebuilt checkpoint for user {user_id} from MySQL")
```
