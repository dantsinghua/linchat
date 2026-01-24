# 行为模型定义

本文档定义大模型聊天平台涉及的所有原子业务动作。
基于功能规格说明(spec.md)设计，已移除conversation相关逻辑。

---

## 一、用户认证相关行为

### 1.1 获取验证码（B_AUTH_001）

```伪代码
@行为(B_AUTH_001, "获取验证码")
  @对应需求: FR-002
  @描述: "生成图形验证码，有效期2分钟"
  
  @输入参数:
    - width: Integer = 120
    - height: Integer = 40
  
  @输出结果:
    - 成功: {captcha_id, captcha_image}
  
  @处理逻辑:
    1. 生成4位随机验证码文本
    2. 生成UUID作为captcha_id
    3. 存入Redis，设置2分钟过期
    4. 渲染图片并转Base64
    5. 返回结果

@代码模板(Python):
```python
async def generate_captcha(width: int = 120, height: int = 40) -> dict:
    captcha_text = ''.join(random.choices(string.ascii_uppercase + string.digits, k=4))
    captcha_id = str(uuid.uuid4())
    
    await redis.setex(f"auth:captcha:{captcha_id}", 120, captcha_text)
    
    image = ImageCaptcha(width=width, height=height)
    base64_image = base64.b64encode(image.generate(captcha_text).getvalue()).decode()
    
    return {"captcha_id": captcha_id, "captcha_image": f"data:image/png;base64,{base64_image}"}
```
```

---

### 1.2 用户登录（B_AUTH_002）

```伪代码
@行为(B_AUTH_002, "用户登录")
  @对应需求: FR-001, FR-003, FR-004, FR-008a
  @描述: "验证凭据，检查锁定，生成Token"
  
  @输入参数:
    - username: String
    - password: String  // SM4加密
    - captcha_id: String
    - captcha_code: String
    - timestamp: Long
    - client_ip: String
  
  @输出结果:
    - 成功: {token, expire_time}
    - 失败: CaptchaInvalidException / AccountLockedException / AuthFailedException
  
  @处理逻辑:
    1. 校验验证码（2分钟有效，一次性）
    2. 检查账户锁定（5次失败锁15分钟）
    3. 查询用户并校验状态
    4. SM4解密密码，SM3比对哈希
    5. 失败则递增计数，达5次锁定
    6. 成功则生成SM4加密Token
    7. Token存Redis，1小时TTL
    8. 重置失败计数，更新登录信息

@代码模板(Python):
```python
async def login(request: LoginRequest) -> LoginResult:
    # 1. 验证码校验
    cached = await redis.get(f"auth:captcha:{request.captcha_id}")
    if not cached or cached.upper() != request.captcha_code.upper():
        raise CaptchaInvalidException("验证码错误或已过期")
    await redis.delete(f"auth:captcha:{request.captcha_id}")
    
    # 2. 锁定检查
    user = await user_repo.find_by_username(request.username)
    if user and user.lock_until and user.lock_until > datetime.now():
        remaining = (user.lock_until - datetime.now()).seconds // 60 + 1
        raise AccountLockedException(f"账户已锁定，请{remaining}分钟后重试")
    
    # 3-4. 用户校验
    if not user:
        await handle_login_failure(request.username)
        raise AuthFailedException("用户名或密码错误")
    
    if user.status != 1:
        raise UserDisabledException("账户已被禁用")
    
    decrypted_pwd = sm4_decrypt(request.password)
    if sm3_hash(decrypted_pwd) != user.password_hash:
        await handle_login_failure_with_user(user)
        raise AuthFailedException("用户名或密码错误")
    
    # 6-7. 生成Token并存储（双重过期机制：24小时绝对 + 1小时无操作）
    token = sm4_encrypt(f"{request.username}|{request.password}|{request.timestamp}")
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    login_time = datetime.now()

    await redis.setex(f"auth:token:{token_hash}", 3600, json.dumps({
        "user_id": user.user_id,
        "username": user.username,
        "login_time": login_time.isoformat(),  # 用于24小时绝对过期检查
        "last_active_time": login_time.isoformat(),
        "login_ip": request.client_ip
    }))

    # Token 通过 httpOnly Cookie 返回，禁止 localStorage 存储
    
    # 8. 重置计数，更新登录信息
    user.login_fail_count = 0
    user.lock_until = None
    user.last_login_time = datetime.now()
    user.last_login_ip = request.client_ip
    await user_repo.update(user)
    
    return LoginResult(token=token, expire_time=datetime.now() + timedelta(hours=1))


async def handle_login_failure_with_user(user: User):
    user.login_fail_count += 1
    if user.login_fail_count >= 5:
        user.lock_until = datetime.now() + timedelta(minutes=15)
        user.login_fail_count = 0
    await user_repo.update(user)
```
```

---

### 1.3 Token鉴权验证（B_AUTH_003）

```伪代码
@行为(B_AUTH_003, "Token鉴权验证")
  @对应需求: FR-004, FR-005
  @描述: "验证Token有效性，采用双重过期机制：24小时绝对过期 + 1小时无操作过期"

  @输入参数:
    - token: String (从 httpOnly Cookie 获取)

  @输出结果:
    - 成功: {user_id, username}
    - 失败: TokenMissingException / TokenExpiredException

  @处理逻辑:
    1. Token空值检查
    2. SM4解密验证格式
    3. Redis查询Token信息
    4. 检查24小时绝对过期
    5. 刷新TTL（不超过24小时边界）
    6. 返回用户信息

@代码模板(Python):
```python
async def verify_token(token: str) -> dict:
    if not token:
        raise TokenMissingException("请先登录")

    try:
        sm4_decrypt(token)
    except:
        raise TokenInvalidException("Token无效")

    token_hash = hashlib.sha256(token.encode()).hexdigest()
    token_data = await redis.get(f"auth:token:{token_hash}")

    if not token_data:
        raise TokenExpiredException("登录已过期，请重新登录")

    token_info = json.loads(token_data)
    login_time = datetime.fromisoformat(token_info["login_time"])

    # 检查24小时绝对过期
    elapsed = (datetime.now() - login_time).total_seconds()
    if elapsed >= 86400:  # 24小时
        await redis.delete(f"auth:token:{token_hash}")
        raise TokenExpiredException("登录已超过24小时，请重新登录")

    # 刷新TTL（1小时无操作过期，但不超过24小时边界）
    remaining_absolute = 86400 - elapsed
    ttl = min(3600, int(remaining_absolute))
    await redis.expire(f"auth:token:{token_hash}", ttl)

    return token_info
```
```

---

## 二、聊天功能相关行为

### 2.1 发送消息并获取响应（B_CHAT_001）

```伪代码
@行为(B_CHAT_001, "发送消息并获取响应")
  @对应需求: FR-010, FR-011, FR-016
  @描述: "用户发送消息，触发Agent执行，返回流式响应"
  
  @输入参数:
    - user_id: Long
    - content: String  // 最大4000字符
  
  @输出结果:
    - 成功: AsyncGenerator[StreamChunk]
    - 失败: ContentTooLongException / EmptyMessageException
  
  @设计说明:
    - 合并发送和响应为一个流程
    - Redis Checkpoint自动管理对话历史
    - MySQL双写用于持久化
  
  @处理逻辑:
    1. 校验消息长度（≤4000）和非空
    2. 生成request_id和thread_id
    3. 调用Agent执行并流式返回
    4. 消息持久化到MySQL（在Agent执行中完成）

@代码模板(Python):
```python
async def send_message(user_id: int, content: str) -> AsyncGenerator[StreamChunk, None]:
    # 1. 校验
    if not content.strip():
        raise EmptyMessageException("消息内容不能为空")
    if len(content) > 4000:
        raise ContentTooLongException("消息长度不能超过4000字符")
    
    # 2. 生成标识
    request_id = f"req_{uuid.uuid4().hex[:16]}"
    thread_id = f"user_{user_id}"
    
    # 3. 调用Agent执行（消息持久化在execute_agent中完成）
    async for chunk in execute_agent(
        user_id=user_id,
        thread_id=thread_id,
        request_id=request_id,
        user_message=content
    ):
        yield chunk
```
```

---

### 2.2 执行LangGraph Agent（B_CHAT_002）

```伪代码
@行为(B_CHAT_002, "执行LangGraph Agent")
  @对应需求: FR-011, FR-017, FR-018, FR-019, FR-020
  @描述: "调用LangGraph Agent处理消息，使用Redis Checkpoint管理对话状态，流式输出"
  
  @输入参数:
    - user_id: Long
    - thread_id: String  // "user_{user_id}"
    - request_id: String
    - user_message: String
  
  @输出结果:
    - 成功: AsyncGenerator[StreamChunk]
  
  @关键设计:
    - Redis Checkpoint: 自动管理对话历史，无需手动加载
    - 双写策略: Checkpoint管理状态 + MySQL持久化记录
  
  @处理逻辑:
    1. 创建执行记录（用于监控）
    2. 初始化Langfuse追踪
    3. 使用Redis Checkpointer编译Graph
    4. 调用Agent（Checkpoint自动加载历史）
    5. 流式执行并记录节点详情
    6. 保存消息到MySQL（持久化）
    7. 更新执行记录和用户统计

@代码模板(Python):
```python
from langgraph.checkpoint.redis import RedisSaver
from langgraph.prebuilt import create_react_agent
from langfuse.callback import CallbackHandler

# 全局Redis Checkpointer（应用启动时初始化）
redis_checkpointer = RedisSaver.from_conn_string(
    settings.REDIS_URL,
    ttl={"default_ttl": 60 * 24, "refresh_on_read": True}  # 24小时过期
)
redis_checkpointer.setup()  # 首次运行需要

async def execute_agent(
    user_id: int,
    thread_id: str,
    request_id: str,
    user_message: str
) -> AsyncGenerator[StreamChunk, None]:
    
    execution_uuid = str(uuid.uuid4())
    start_time = datetime.now()
    
    # 1. 创建执行记录（MySQL，用于监控）
    execution = LangGraphExecution(
        execution_uuid=execution_uuid,
        request_id=request_id,
        user_id=user_id,
        thread_id=thread_id,
        graph_name="react_agent",
        status="pending",
        start_time=start_time,
        input_data={"message": user_message}
    )
    await execution_repo.create(execution)
    
    try:
        # 2. Langfuse追踪
        langfuse_handler = CallbackHandler(
            trace_name=f"chat_{request_id}",
            user_id=str(user_id),
            session_id=thread_id
        )
        
        # 3. 创建Agent（使用Redis Checkpointer）
        model = ChatOpenAI(
            base_url=settings.LLM_API_BASE,
            model=settings.LLM_MODEL,
            streaming=True,
            callbacks=[langfuse_handler]
        )
        
        # 编译时传入checkpointer，自动管理对话历史
        agent = create_react_agent(
            model=model, 
            tools=[],
            checkpointer=redis_checkpointer  # Redis自动管理状态
        )
        
        # 4. 配置thread_id（Checkpoint会自动加载该用户的历史消息）
        config = {
            "configurable": {"thread_id": thread_id},
            "callbacks": [langfuse_handler]
        }
        
        # 5. 流式执行（无需手动加载历史，Checkpoint自动处理）
        execution.status = "running"
        await execution_repo.update(execution)
        
        full_response = ""
        node_executions = {"nodes": [], "execution_path": []}
        total_prompt_tokens = 0
        total_completion_tokens = 0
        
        # 直接发送当前消息，历史由Checkpoint自动注入
        input_message = {"messages": [HumanMessage(content=user_message)]}
        
        async for event in agent.astream_events(input_message, config=config, version="v2"):
            if event["event"] == "on_chat_model_stream":
                chunk = event["data"]["chunk"]
                if hasattr(chunk, "content") and chunk.content:
                    full_response += chunk.content
                    yield StreamChunk(type="content", content=chunk.content)
            
            # 记录节点执行（用于监控）
            elif event["event"] == "on_chain_start":
                node_name = event.get("name", "unknown")
                node_executions["execution_path"].append(node_name)
            
            # Token统计
            elif event["event"] == "on_llm_end":
                if hasattr(event["data"], "output"):
                    usage = getattr(event["data"]["output"], "usage_metadata", {})
                    total_prompt_tokens += usage.get("input_tokens", 0)
                    total_completion_tokens += usage.get("output_tokens", 0)
        
        yield StreamChunk(type="done", content="")
        
        # 6. 保存消息到MySQL（持久化，用于审计和历史查询）
        end_time = datetime.now()
        duration_ms = int((end_time - start_time).total_seconds() * 1000)
        
        # 保存用户消息
        max_seq = await message_repo.get_max_sequence(user_id) or 0
        user_msg = Message(
            message_uuid=str(uuid.uuid4()),
            user_id=user_id,
            role="user",
            content=user_message,
            request_id=request_id,
            sequence=max_seq + 1,
            status=1
        )
        await message_repo.create(user_msg)
        
        # 保存AI响应
        assistant_msg = Message(
            message_uuid=str(uuid.uuid4()),
            user_id=user_id,
            role="assistant",
            content=full_response,
            request_id=request_id,
            response_time_ms=duration_ms,
            prompt_tokens=total_prompt_tokens,
            completion_tokens=total_completion_tokens,
            model_name=settings.LLM_MODEL,
            sequence=max_seq + 2,
            status=1
        )
        await message_repo.create(assistant_msg)
        
        # 7. 更新统计
        await user_repo.add_message_count(user_id, 2)
        await user_repo.add_tokens(user_id, total_prompt_tokens + total_completion_tokens)
        
        # 更新执行记录
        execution.status = "completed"
        execution.end_time = end_time
        execution.duration_ms = duration_ms
        execution.output_data = {"response": full_response}
        execution.node_executions = node_executions
        execution.total_prompt_tokens = total_prompt_tokens
        execution.total_completion_tokens = total_completion_tokens
        execution.langfuse_trace_id = langfuse_handler.trace_id
        await execution_repo.update(execution)
        
    except Exception as e:
        execution.status = "failed"
        execution.error_type = type(e).__name__
        execution.error_message = str(e)
        await execution_repo.update(execution)
        yield StreamChunk(type="error", content="服务暂时不可用，请稍后重试")
```
```

**关键点说明：**

1. **无需手动加载历史**：Redis Checkpoint自动管理，调用时只需传当前消息
2. **双写策略**：Checkpoint管理实时状态，MySQL持久化用于审计
3. **thread_id**：使用`user_{user_id}`，确保用户数据隔离
4. **TTL配置**：24小时无活动自动清理对话状态

---

### 2.3 加载历史消息（B_CHAT_003）

```伪代码
@行为(B_CHAT_003, "加载历史消息")
  @对应需求: FR-015, FR-016
  @描述: "根据用户ID加载历史消息"
  
  @输入参数:
    - user_id: Long
    - limit: Integer = 50
    - before_sequence: Integer = None
  
  @输出结果:
    - 成功: List[MessageVO]
  
  @处理逻辑:
    1. 参数处理（limit最大100）
    2. 根据user_id查询消息（数据隔离）
    3. 支持游标分页（before_sequence）
    4. 按sequence正序返回

@代码模板(Python):
```python
async def load_history_messages(
    user_id: int,
    limit: int = 50,
    before_sequence: int = None
) -> List[MessageVO]:
    limit = min(limit, 100)
    
    if before_sequence:
        messages = await message_repo.find_by_user_before_sequence(
            user_id=user_id,
            before_sequence=before_sequence,
            limit=limit
        )
    else:
        messages = await message_repo.find_latest_by_user(
            user_id=user_id,
            limit=limit
        )
    
    messages.reverse()  # 返回正序
    return [MessageVO.from_entity(m) for m in messages]
```
```

---

## 三、行为模型索引

| 行为编码 | 行为名称 | 对应需求 | 关键技术 |
|---------|---------|---------|---------|
| B_AUTH_001 | 获取验证码 | FR-002 | Redis 2分钟TTL |
| B_AUTH_002 | 用户登录 | FR-001,003,004,008a | SM4加密, Redis Token |
| B_AUTH_003 | Token鉴权验证 | FR-004,005 | Redis TTL刷新 |
| B_CHAT_001 | 发送消息并获取响应 | FR-010,011,016 | 消息校验, 触发Agent |
| B_CHAT_002 | 执行LangGraph Agent | FR-011,017-020 | **Redis Checkpoint**, Langfuse |
| B_CHAT_003 | 加载历史消息 | FR-015,016 | MySQL查询, 数据隔离 |

---

## 四、Redis Checkpoint 关键说明

### 为什么不需要手动加载历史？

```
传统方式：
1. 从MySQL加载历史消息
2. 构建messages数组
3. 发送给LLM
4. 保存响应到MySQL

Redis Checkpoint方式：
1. 调用Agent（传入thread_id）
2. Checkpoint自动加载该thread的历史
3. LLM响应
4. Checkpoint自动保存新状态
5. （可选）双写到MySQL用于持久化
```

### 双写策略

| 存储 | 用途 | 管理方式 |
|-----|------|---------|
| Redis Checkpoint | 对话状态、上下文 | LangGraph自动管理 |
| MySQL message表 | 持久化、审计、历史查询 | 代码显式写入 |

**已移除**：原conversation相关操作已删除，message直接关联user，对话状态由Redis Checkpoint管理。
