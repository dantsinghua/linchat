# 规则模型定义

本文档定义大模型聊天平台的业务规则。基于spec.md设计。

---

## 一、验证码规则

### R_CAPTCHA_001 验证码有效期规则

```伪代码
@规则(R_CAPTCHA_001, "验证码有效期规则")
  @对应需求: FR-002
  @描述: "验证码生成后2分钟内有效"
  @参数: CAPTCHA_EXPIRE_SECONDS = 120

@代码片段:
await redis.setex(f"auth:captcha:{captcha_id}", 120, captcha_text)
```

### R_CAPTCHA_002 验证码校验规则

```伪代码
@规则(R_CAPTCHA_002, "验证码校验规则")
  @对应需求: FR-002
  @描述: "验证码一次性使用，校验后立即删除"
  
  @校验逻辑:
    如果 Redis中不存在 → "验证码已过期，请刷新"
    如果 不匹配（忽略大小写）→ "验证码错误"
    校验成功 → 立即删除

@代码片段:
cached = await redis.get(f"auth:captcha:{captcha_id}")
if not cached:
    raise CaptchaInvalidException("验证码已过期，请刷新")
if cached.upper() != input_code.upper():
    raise CaptchaInvalidException("验证码错误")
await redis.delete(f"auth:captcha:{captcha_id}")
```

### R_CAPTCHA_003 验证码自动刷新规则

```伪代码
@规则(R_CAPTCHA_003, "验证码自动刷新规则")
  @对应需求: FR-002a
  @描述: "前端在验证码过期前10秒自动刷新"
  @参数: CAPTCHA_REFRESH_INTERVAL = 110秒

@代码片段(前端):
setInterval(refreshCaptcha, 110 * 1000)
```

---

## 二、登录锁定规则

### R_LOGIN_001 登录失败锁定规则

```伪代码
@规则(R_LOGIN_001, "登录失败锁定规则")
  @对应需求: FR-008a
  @描述: "连续5次登录失败后，账户锁定15分钟"
  @参数:
    - MAX_FAIL_COUNT = 5
    - LOCK_MINUTES = 15
  
  @校验逻辑:
    如果 lock_until > 当前时间 → 拒绝登录，提示剩余锁定时间
    
    登录失败时:
      login_fail_count += 1
      如果 >= 5 → 设置 lock_until = now + 15分钟
    
    登录成功时:
      login_fail_count = 0
      lock_until = null

@代码片段:
# 检查锁定
if user.lock_until and user.lock_until > datetime.now():
    remaining = (user.lock_until - datetime.now()).seconds // 60 + 1
    raise AccountLockedException(f"账户已锁定，请{remaining}分钟后重试")

# 失败处理
user.login_fail_count += 1
if user.login_fail_count >= 5:
    user.lock_until = datetime.now() + timedelta(minutes=15)
    user.login_fail_count = 0
```

---

## 三、Token规则

### R_TOKEN_001 Token生成规则

```伪代码
@规则(R_TOKEN_001, "Token生成规则")
  @对应需求: FR-003
  @描述: "使用国密SM4加密生成Token"
  @Token结构: "{username}|{password}|{timestamp}"
```

### R_TOKEN_002 Token有效性校验规则

```伪代码
@规则(R_TOKEN_002, "Token有效性校验规则")
  @对应需求: FR-005
  @描述: "每次请求必须校验Token"
  
  @校验逻辑:
    1. Token不能为空 → "请先登录"
    2. SM4解密失败 → "Token无效"
    3. Redis中不存在 → "登录已过期"
```

### R_TOKEN_003 Token双重过期规则

```伪代码
@规则(R_TOKEN_003, "Token双重过期规则")
  @对应需求: FR-004
  @描述: "采用双重过期机制：(1) 绝对有效期24小时；(2) 无操作1小时自动过期"
  @参数:
    - TOKEN_IDLE_TIMEOUT = 3600秒（1小时无操作）
    - TOKEN_ABSOLUTE_TIMEOUT = 86400秒（24小时绝对过期）

  @机制:
    登录时:
      → 记录 login_time 到 token_data
      → Redis.setex(token_key, 3600, data)

    验证时:
      → 检查 now - login_time >= 24小时 → 强制失效
      → 否则 Redis.expire(token_key, 3600)  # 刷新TTL，但不超过24小时

@代码片段:
# 登录时
token_data = {
    "user_id": user.user_id,
    "username": user.username,
    "login_time": datetime.now().isoformat(),  # 记录登录时间
    "last_active_time": datetime.now().isoformat()
}
await redis.setex(f"auth:token:{token_hash}", 3600, json.dumps(token_data))

# 验证时
token_info = json.loads(await redis.get(f"auth:token:{token_hash}"))
login_time = datetime.fromisoformat(token_info["login_time"])

# 检查24小时绝对过期
if (datetime.now() - login_time).total_seconds() >= 86400:
    await redis.delete(f"auth:token:{token_hash}")
    raise TokenExpiredException("登录已超过24小时，请重新登录")

# 刷新1小时无操作过期（但不超过24小时边界）
remaining_absolute = 86400 - (datetime.now() - login_time).total_seconds()
ttl = min(3600, int(remaining_absolute))
await redis.expire(f"auth:token:{token_hash}", ttl)
```

---

## 四、消息规则

### R_MSG_001 消息长度限制规则

```伪代码
@规则(R_MSG_001, "消息长度限制规则")
  @描述: "单条消息最大4000字符"
  @参数: MAX_MESSAGE_LENGTH = 4000

@代码片段:
if len(content) > 4000:
    raise ContentTooLongException("消息长度不能超过4000字符")
```

### R_MSG_002 空消息拦截规则

```伪代码
@规则(R_MSG_002, "空消息拦截规则")
  @描述: "阻止发送空消息或仅包含空白字符的消息"

@代码片段:
if not content.strip():
    raise EmptyMessageException("消息内容不能为空")
```

---

## 五、数据隔离规则

### R_DATA_001 用户数据隔离规则

```伪代码
@规则(R_DATA_001, "用户数据隔离规则")
  @对应需求: FR-016
  @描述: "用户只能访问自己的数据，通过user_id过滤"
  
  @实现方式:
    - message表通过user_id直接关联用户
    - 所有查询必须携带user_id条件
    - user_id从认证上下文获取，不从前端参数获取

@代码片段:
# 加载消息时强制user_id过滤
messages = await message_repo.find_by_user_id(
    user_id=request.state.user_id,  # 从认证上下文获取
    limit=50
)
```

---

## 六、Agent执行规则

### R_AGENT_001 Agent执行超时规则

```伪代码
@规则(R_AGENT_001, "Agent执行超时规则")
  @描述: "Agent执行超时处理"
  @参数:
    - LLM_CALL_TIMEOUT = 60秒
    - AGENT_TOTAL_TIMEOUT = 300秒

@代码片段:
async with asyncio.timeout(300):
    async for event in agent.astream_events(...):
        yield event
```

### R_STREAM_001 流式响应中断处理规则

```伪代码
@规则(R_STREAM_001, "流式响应中断处理规则")
  @描述: "流式响应中断时，保留已接收内容，标记消息状态为中断"
  
  @处理逻辑:
    1. 保存部分响应到message表（status=3）
    2. 更新execution状态为interrupted
    3. 向前端发送error类型chunk
```

---

## 七、规则索引

| 规则编码 | 规则名称 | 对应需求 | 适用行为 |
|---------|---------|---------|---------|
| R_CAPTCHA_001 | 验证码有效期规则 | FR-002 | B_AUTH_001 |
| R_CAPTCHA_002 | 验证码校验规则 | FR-002 | B_AUTH_002 |
| R_CAPTCHA_003 | 验证码自动刷新规则 | FR-002a | 前端 |
| R_LOGIN_001 | 登录失败锁定规则 | FR-008a | B_AUTH_002 |
| R_TOKEN_001 | Token生成规则 | FR-003 | B_AUTH_002 |
| R_TOKEN_002 | Token有效性校验规则 | FR-005 | B_AUTH_003 |
| R_TOKEN_003 | Token无操作过期规则 | FR-004 | B_AUTH_002,003 |
| R_MSG_001 | 消息长度限制规则 | - | B_CHAT_001 |
| R_MSG_002 | 空消息拦截规则 | - | B_CHAT_001 |
| R_DATA_001 | 用户数据隔离规则 | FR-016 | B_CHAT_001,003 |
| R_AGENT_001 | Agent执行超时规则 | - | B_CHAT_002 |
| R_STREAM_001 | 流式响应中断处理规则 | - | B_CHAT_002 |

---

## 八、规则依赖链

```
登录流程: R_CAPTCHA_002 → R_LOGIN_001 → R_TOKEN_001 → R_TOKEN_003

验证流程: R_TOKEN_002 → R_TOKEN_003

消息流程: R_MSG_002 → R_MSG_001 → R_DATA_001

Agent流程: R_AGENT_001 → R_STREAM_001
```

---

## 九、配置参数汇总

```yaml
auth:
  captcha:
    expire-seconds: 120        # 2分钟
    refresh-before: 10         # 提前10秒刷新
  login:
    max-fail-count: 5
    lock-minutes: 15
  token:
    idle-timeout-seconds: 3600    # 1小时无操作过期
    absolute-timeout-seconds: 86400  # 24小时绝对过期

message:
  max-length: 4000

agent:
  llm-call-timeout: 60
  total-timeout: 300
```
