# 数据模型定义

本文档定义大模型聊天平台涉及的所有数据存储结构。
基于功能规格说明(spec.md)设计，整合LangGraph Redis Checkpointer。

---

## 一、存储架构概览

```
┌─────────────────────────────────────────────────────────────────┐
│                         Redis                                   │
├─────────────────────────────────────────────────────────────────┤
│  auth:token:{hash}        → 用户Token（1小时TTL）               │
│  auth:captcha:{id}        → 验证码（2分钟TTL）                  │
│  auth:fail:{username}     → 登录失败计数（15分钟TTL）           │
│  langgraph:checkpoint:*   → LangGraph对话状态（RedisSaver管理） │
│  langgraph:writes:*       → LangGraph pending writes            │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│                        PostgreSQL                               │
├─────────────────────────────────────────────────────────────────┤
│  sys_user                 → 用户表                              │
│  message                  → 消息表（持久化聊天记录）             │
│  langgraph_execution      → 执行监控表（可选，用于Langfuse追踪） │
└─────────────────────────────────────────────────────────────────┘
```

**设计决策：**
- **LangGraph Checkpoint → Redis**：对话状态用Redis管理，高性能、支持TTL
- **Message → PostgreSQL**：消息持久化到关系型数据库，支持历史查询
- **两者分离**：Checkpoint是运行时状态（可丢失），Message是业务数据（不可丢失）

---

## 二、PostgreSQL 实体定义

### 2.1 用户表（sys_user）

```伪代码
@实体(sys_user, "系统用户表")
  
  // ========== 主键 ==========
  @字段(user_id, BIGINT, 主键, 自增)
  
  // ========== 认证信息 ==========
  @字段(username, VARCHAR(50), 非空, 唯一)
  @字段(password_hash, VARCHAR(255), 非空)  // SM3哈希
  
  // ========== 账户状态 ==========
  @字段(status, TINYINT, 非空, 默认(1))     // 0-禁用，1-启用
  @字段(login_fail_count, INT, 非空, 默认(0))
  @字段(lock_until, DATETIME)               // 锁定截止时间
  
  // ========== 聊天统计 ==========
  @字段(message_count, INT, 非空, 默认(0))
  @字段(total_tokens, BIGINT, 非空, 默认(0))
  @字段(last_active_time, DATETIME)
  
  // ========== 登录信息 ==========
  @字段(last_login_time, DATETIME)
  @字段(last_login_ip, VARCHAR(50))
  
  // ========== 审计字段 ==========
  @字段(created_time, DATETIME, 非空, 默认(当前时间))
  @字段(updated_time, DATETIME, 非空, 默认(当前时间))
  
  // ========== 索引 ==========
  @索引(uk_username, [username], 唯一索引)

@初始化数据:
  INSERT INTO sys_user (username, password_hash, status) 
  VALUES ('admin', '{SM3_HASH(!9871229Qing)}', 1);
```

---

### 2.2 消息表（message）

```伪代码
@实体(message, "聊天消息表，持久化存储")
  
  // ========== 主键 ==========
  @字段(message_id, BIGINT, 主键, 自增)
  @字段(message_uuid, VARCHAR(36), 非空, 唯一)
  
  // ========== 关联字段 ==========
  @字段(user_id, BIGINT, 非空)  // 数据隔离
  
  // ========== 消息内容 ==========
  @字段(role, VARCHAR(20), 非空)      // user/assistant/system
  @字段(content, LONGTEXT, 非空)
  
  // ========== 监控埋点（FR-026）==========
  @字段(request_id, VARCHAR(64))       // 链路追踪
  @字段(response_time_ms, INT)         // 响应耗时
  @字段(prompt_tokens, INT, 默认(0))
  @字段(completion_tokens, INT, 默认(0))
  @字段(model_name, VARCHAR(100))
  
  // ========== 扩展字段（FR-027）==========
  @字段(extra_data, JSON)
  
  // ========== 排序与状态 ==========
  @字段(sequence, INT, 非空)           // 用户内递增
  @字段(status, TINYINT, 非空, 默认(1)) // 0-失败,1-正常,2-生成中,3-中断
  
  // ========== 审计字段 ==========
  @字段(created_time, DATETIME, 非空, 默认(当前时间))
  
  // ========== 索引 ==========
  @索引(uk_message_uuid, [message_uuid], 唯一索引)
  @索引(idx_user_sequence, [user_id, sequence], 联合索引)
  @索引(idx_user_created, [user_id, created_time], 联合索引)
  @索引(idx_request_id, [request_id], 普通索引)
```

---

### 2.3 执行监控表（langgraph_execution）- 可选

> 此表用于详细的执行监控和Langfuse集成，如果只需要基本功能可以省略。

```伪代码
@实体(langgraph_execution, "LangGraph执行监控表")
  
  @字段(execution_id, BIGINT, 主键, 自增)
  @字段(execution_uuid, VARCHAR(36), 非空, 唯一)
  
  // ========== 关联 ==========
  @字段(request_id, VARCHAR(64), 非空)  // 关联message
  @字段(user_id, BIGINT, 非空)
  @字段(thread_id, VARCHAR(64), 非空)   // "user_{user_id}"
  
  // ========== 执行信息 ==========
  @字段(graph_name, VARCHAR(100), 非空)
  @字段(run_id, VARCHAR(64))
  @字段(status, VARCHAR(20), 非空)      // pending/running/completed/failed
  @字段(start_time, DATETIME, 非空)
  @字段(end_time, DATETIME)
  @字段(duration_ms, INT)
  
  // ========== 详情（JSON）==========
  @字段(input_data, JSON)
  @字段(output_data, JSON)
  @字段(node_executions, JSON)          // 节点执行详情
  
  // ========== Token统计 ==========
  @字段(total_prompt_tokens, INT, 默认(0))
  @字段(total_completion_tokens, INT, 默认(0))
  @字段(llm_call_count, INT, 默认(0))
  
  // ========== 错误信息 ==========
  @字段(error_type, VARCHAR(100))
  @字段(error_message, TEXT)
  
  // ========== Langfuse ==========
  @字段(langfuse_trace_id, VARCHAR(64))
  @字段(langfuse_url, VARCHAR(500))
  
  // ========== 索引 ==========
  @索引(idx_request_id, [request_id])
  @索引(idx_user_id, [user_id])
  @索引(idx_thread_id, [thread_id])
```

---

## 三、Redis 缓存设计

### 3.1 认证相关

```伪代码
// ===== Token缓存（双重过期机制）=====
@键: "auth:token:{token_hash}"
@值: {
  "user_id": 1,
  "username": "admin",
  "login_time": "2026-01-25T10:00:00Z",    // 登录时间，用于24小时绝对过期检查
  "last_active_time": "2026-01-25T10:30:00Z",
  "login_ip": "192.168.1.100"
}
@TTL: 动态计算，min(3600秒, 24小时绝对过期剩余时间)
@过期机制:
  - 无操作过期: 1小时无操作自动过期，有操作时刷新TTL
  - 绝对过期: 登录后24小时强制失效，刷新操作不延长此期限
@存储位置: httpOnly Cookie（禁止 localStorage）

// ===== 验证码缓存 =====
@键: "auth:captcha:{captcha_id}"
@值: "ABCD"
@TTL: 120秒（2分钟）

// ===== 登录失败计数 =====
@键: "auth:fail:{username}"
@值: 3
@TTL: 900秒（15分钟）
```

### 3.2 LangGraph Checkpoint（由RedisSaver管理）

```伪代码
// ===== Checkpoint数据（RedisSaver自动管理）=====
@键模式: 由langgraph-checkpoint-redis内部管理
  - "langgraph:checkpoint:{thread_id}:{checkpoint_id}"
  - "langgraph:writes:{thread_id}:{checkpoint_id}"
  - "langgraph:metadata:{thread_id}"

@thread_id格式: "user_{user_id}"

@存储内容:
  - channel_values: 当前状态值（包含messages历史）
  - channel_versions: 版本信息
  - pending_sends: 待处理发送
  - metadata: 检查点元数据

@TTL配置:
  default_ttl: 1440        # 24小时后过期
  refresh_on_read: true    # 读取时刷新TTL
```

**Checkpoint vs Message 的区别：**

| 项目 | LangGraph Checkpoint (Redis) | Message (PostgreSQL) |
|------|------------------------------|-----------------|
| 用途 | 运行时对话状态 | 持久化聊天记录 |
| 生命周期 | 可配置TTL，可丢失 | 永久保存 |
| 内容 | 完整状态快照（含历史） | 单条消息 |
| 查询 | 按thread_id | 按user_id + 分页 |
| 恢复 | 支持time-travel | 支持历史回溯 |

---

## 四、实体关系图

```
┌─────────────────────────┐
│       sys_user          │
├─────────────────────────┤
│ user_id (PK)            │───────────────────────────────┐
│ username                │                               │
│ password_hash           │                               │
│ message_count           │                               │
│ total_tokens            │                               │
└───────────┬─────────────┘                               │
            │                                             │
            │ 1:N                                         │
            ▼                                             │
┌─────────────────────────┐                               │
│        message          │                               │
├─────────────────────────┤                               │
│ message_id (PK)         │                               │
│ user_id (FK) ───────────┼───────────────────────────────┘
│ role, content           │
│ request_id              │
│ sequence                │
└─────────────────────────┘

            ┌─────────────────────────────────────────────┐
            │                   Redis                     │
            ├─────────────────────────────────────────────┤
            │  auth:token:*     ← Token缓存              │
            │  auth:captcha:*   ← 验证码                  │
            │                                             │
            │  ┌─────────────────────────────────────┐   │
            │  │   LangGraph RedisSaver 管理区域     │   │
            │  │                                     │   │
            │  │  thread_id = "user_{user_id}"      │   │
            │  │                                     │   │
            │  │  checkpoint:* ← 对话状态快照        │   │
            │  │  writes:*     ← pending writes     │   │
            │  │  metadata:*   ← 元数据              │   │
            │  └─────────────────────────────────────┘   │
            └─────────────────────────────────────────────┘
```

---

## 五、LangGraph RedisSaver 配置

```python
from langgraph.checkpoint.redis import RedisSaver

# Redis连接配置
REDIS_URL = "redis://localhost:6379"

# TTL配置：24小时过期，读取时刷新
TTL_CONFIG = {
    "default_ttl": 60 * 24,  # 24小时（分钟）
    "refresh_on_read": True,
}

# 创建checkpointer
def get_checkpointer():
    checkpointer = RedisSaver.from_conn_string(
        REDIS_URL,
        ttl=TTL_CONFIG
    )
    checkpointer.setup()  # 首次运行需要初始化索引
    return checkpointer

# thread_id约定
def get_thread_id(user_id: int) -> str:
    return f"user_{user_id}"
```

---

## 六、数据流说明

### 发送消息时的数据流

```
1. 用户发送消息
   │
   ├─→ MySQL: 保存用户消息到message表 (role=user)
   │
   ├─→ Redis: RedisSaver读取checkpoint获取对话历史
   │
   ├─→ LangGraph: 执行Agent，流式生成响应
   │
   ├─→ Redis: RedisSaver保存新checkpoint（包含本轮对话）
   │
   └─→ MySQL: 保存AI响应到message表 (role=assistant)
```

### 数据一致性策略

```
Checkpoint (Redis)          Message (MySQL)
     │                           │
     │  运行时状态                │  持久化记录
     │  可能丢失                  │  不可丢失
     │                           │
     └───────────┬───────────────┘
                 │
                 ▼
         如果Checkpoint丢失：
         从MySQL的message表重建对话历史
```

---

## 七、配置参数汇总

```yaml
# 数据库配置 (统一使用 PostgreSQL，与 Langfuse 共用)
database:
  # PostgreSQL (开发/生产环境统一)
  url: "postgresql+asyncpg://user:pass@localhost:5432/linchat"

# Redis配置
redis:
  url: "redis://localhost:6379"
  
  # 认证相关TTL
  auth:
    token_ttl: 3600          # Token: 1小时
    captcha_ttl: 120         # 验证码: 2分钟
    fail_count_ttl: 900      # 失败计数: 15分钟
  
  # LangGraph Checkpoint TTL
  checkpoint:
    default_ttl: 1440        # 24小时
    refresh_on_read: true

# LangGraph配置
langgraph:
  thread_id_prefix: "user_"  # thread_id = "user_{user_id}"
```

---

## 八、模型元信息

```yaml
版本: "1.2.0"
基于: "spec.md"

存储分层:
  Redis:
    - Token/验证码/失败计数
    - LangGraph Checkpoint (RedisSaver)
  PostgreSQL:
    - sys_user (用户)
    - message (消息持久化)
    - langgraph_execution (监控，可选)

核心决策:
  - Checkpoint用Redis: 高性能，支持TTL
  - Message用MySQL: 持久化，支持复杂查询
  - 两者解耦: Checkpoint丢失可从Message重建

依赖包:
  - langgraph-checkpoint-redis
  - redis / aioredis
  - sqlalchemy / databases
```
