# Batch batch-11 执行计划

> 生成时间：2026-07-17
> 类型：refactor | 优先级：P1 | 风险：medium
> 预估：3 文件 / 80 行 / 1 session
> 依赖：无（depends_on=[]，无前置）
> SLO 影响：无（blocks_slo=null，但属 performance 类，目标降低 Redis connected_clients 峰值 50%+）
> 基线：main HEAD=a9d1305（已含 batch-04~10+28）

## 1. 任务理解（一句话）

把 `core/redis.py` 的 `get_redis()`/`get_async_redis_client()` 从"每次调用新建独立连接+独立连接池"改为"进程内共享、按事件循环隔离的 aioredis 连接池"，`max_connections` 配置化，从根上消除未 `aclose()` 调用点造成的连接泄漏，且不改动任何调用点签名、不动 Channels DB3 / Celery 同步路径。

## 2. 涉及文件清单与改动预测

| # | 文件 | 当前行数 | 预计改动行数 | 改动类型 | 风险 | 精简潜力 |
|---|------|---------|------------|---------|------|---------|
| 1 | backend/core/redis.py | 200 | +30 -12 | 修改 RedisClient/get_client 内部实现 | 中 | 低（结构清晰，无未用 import） |
| 2 | backend/core/settings.py | 486 | +2 | 新增 REDIS_MAX_CONNECTIONS 配置 | 低 | 低（>300 行但本 batch 不拆，见 §7） |
| 3 | backend/tests/test_redis.py | 0（新建） | +90 | 新增测试 | 低 | — |

关键：**14 个调用点、共 33 处 `await get_redis()`/`get_async_redis_client()` 全部不改**（签名不变）。其中已调用 `.aclose()` 的点（voice_session_service ×4、response_decision_service ×1、search.py、ha_helpers.py）在新实现下 `.aclose()` 变为"仅归还连接到共享池、不销毁池"的安全语义（见 §3 证据）。

## 3. 详细改动计划

### 文件 2 先行：backend/core/settings.py

#### 改动 2.1
- 位置：第 140 行 `REDIS_URL = ...` 之后
- 当前代码：
  ```python
  REDIS_URL = os.getenv("REDIS_URL", "redis://:redis_linchat_123@localhost:6379/0")

  CACHES = {
  ```
- 改动方案：
  ```python
  REDIS_URL = os.getenv("REDIS_URL", "redis://:redis_linchat_123@localhost:6379/0")

  # aioredis 连接池上限（core/redis.py get_redis 共享池）。
  # 需覆盖峰值并发：短命令 + 长命令 + 每个 SSE 订阅/cancel_monitor 各占 1 条 pubsub 连接。
  REDIS_MAX_CONNECTIONS = int(os.getenv("REDIS_MAX_CONNECTIONS", "50"))

  CACHES = {
  ```
- 理由：与既有 `CACHES.CONNECTION_POOL_KWARGS.max_connections=50`（settings.py:148）对齐；env 可覆盖便于压测调参。
- 预估行数：+4

### 文件 1：backend/core/redis.py

#### 改动 1.1 — 顶部新增 import 与按 loop 缓存的池
- 位置：第 6-12 行 import 区
- 当前代码：
  ```python
  import json
  from datetime import timedelta
  from typing import Any

  import redis
  import redis.asyncio as aioredis
  from django.conf import settings
  ```
- 改动方案（新增 asyncio + weakref，`timedelta` 若确无引用则一并删除——执行时用 rg 确认后再删，不确定则保留）：
  ```python
  import asyncio
  import json
  import weakref
  from typing import Any

  import redis
  import redis.asyncio as aioredis
  from django.conf import settings
  ```
- 理由：需要 `asyncio.get_running_loop()` 做 loop 隔离键、`weakref.WeakKeyDictionary` 让 loop 结束后池条目自动回收（防 pytest 多 loop 累积）。
- 预估行数：+2 -1

#### 改动 1.2 — 重写 RedisClient 为共享池
- 位置：第 57-74 行 `class RedisClient`
- 当前代码：
  ```python
  class RedisClient:
      """Redis 客户端封装

      注意：在 WSGI 环境下（Django runserver）使用异步视图时，
      每次请求可能使用不同的事件循环，因此每次调用创建新连接。
      """

      @staticmethod
      async def get_client() -> aioredis.Redis:
          """获取 Redis 客户端连接

          每次调用创建新连接，避免事件循环问题
          """
          return aioredis.from_url(
              settings.REDIS_URL,
              encoding="utf-8",
              decode_responses=True,
          )
  ```
- 改动方案：
  ```python
  # 按事件循环隔离的共享连接池：
  # - 生产 uvicorn 单 loop → 全进程 1 个池，连接受 max_connections 约束
  # - pytest 每个用例独立 loop → 各自建池，loop 回收时 WeakKeyDictionary 自动清理
  _POOLS: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, aioredis.ConnectionPool]" = (
      weakref.WeakKeyDictionary()
  )


  def _get_pool() -> aioredis.ConnectionPool:
      """获取当前事件循环对应的共享连接池（懒创建）。"""
      loop = asyncio.get_running_loop()
      pool = _POOLS.get(loop)
      if pool is None:
          pool = aioredis.ConnectionPool.from_url(
              settings.REDIS_URL,
              encoding="utf-8",
              decode_responses=True,
              max_connections=settings.REDIS_MAX_CONNECTIONS,
          )
          _POOLS[loop] = pool
      return pool


  class RedisClient:
      """Redis 客户端封装（ASGI 安全的共享连接池）。

      ASGI（uvicorn）单事件循环下，进程内复用同一 ConnectionPool；
      每次 get_client() 返回一个绑定共享池的轻量 Redis 包装对象。
      因用显式 connection_pool 构造，Redis.auto_close_connection_pool=False，
      故调用点的 .aclose() 只归还连接、不销毁共享池。
      """

      @staticmethod
      async def get_client() -> aioredis.Redis:
          """返回绑定共享连接池的 Redis 客户端。"""
          return aioredis.Redis(connection_pool=_get_pool())
  ```
- 理由（证据）：
  - redis-py 7.1.0 `Redis.__init__`：传入 `connection_pool` 时 `self.auto_close_connection_pool = False`。
  - `Redis.aclose(close_connection_pool=None)`：`if close_connection_pool or (None and auto_close_connection_pool)` → False 分支只执行 `pool.release(conn)`，**不** `pool.disconnect()`。→ 现有 7 处 `.aclose()` 调用在新实现下安全，无需改调用点。
  - 现状 `from_url()` 每次新建 pool 且 `auto_close_connection_pool=True`，未 `.aclose()` 的调用点（chat/views、rate_limiter、event_service、media/document、cancel_monitor、gpu_lock、inference_service、context_service、consumer_events、speaker_service、tts_router）留下悬挂 pool+空闲连接 → connected_clients 累积。
- 预估行数：+28 -11

#### 改动 1.3 — get_redis / get_async_redis_client 不变
- 位置：第 79-86 行
- 无需改动：两函数继续 `return await RedisClient.get_client()`，签名与返回类型不变 → 33 处调用点零改动。

## 4. 调查步骤（fix 类才需，本 batch 为 refactor，已在研究阶段确认，留档）

- [x] 已确认：`get_redis()` 有 14 个调用文件、33 处调用；其中仅 7 处配 `.aclose()`，其余不关闭 → 泄漏根因。
- [x] 已确认：redis-py 7.1.0 显式池 → `auto_close_connection_pool=False`，`.aclose()` 安全（见 §3 证据）。
- [x] 已确认：Channels 用 `CHANNELS_REDIS_URL`(DB3) + `channels_redis`，与 `core/redis.py`(DB0/REDIS_URL) 完全独立（settings.py:398-402）。
- [x] 已确认：Celery 同步进程不碰 async `get_redis()`；同步走独立 `SyncRedisClient`（本次不动）。memory/tasks.py、task_helpers.py 无 `get_redis` 引用。
- [x] 已确认：pubsub（event_service.subscribe_user_events、cancel_monitor）会从池 checkout 一条专用连接并在 `pubsub.close()` 归还 → 每个活跃 SSE 订阅占 1 连接，纳入 max_connections 容量测算（见 §7 待决策）。
- [x] 已确认：pytest-asyncio 1.3.0 strict、function 作用域 loop；现有测试全部 mock `get_redis`，无真实 Redis 依赖用例。

## 5. 验证计划

### 5.1 自动化验证
- [ ] `pytest backend/tests/test_redis.py -v`（新增，见 §9）
- [ ] `pytest backend/tests/ -v`（全量回归，重点 tests/common、tests/chat、tests/voice、tests/graph）
- [ ] `ruff check backend/core/redis.py backend/core/settings.py backend/tests/test_redis.py`
- [ ] `mypy backend/core/redis.py`（若项目启用；WeakKeyDictionary 泛型注解需通过）

### 5.2 手动验证步骤（安琳执行，只读观测，不停服）
- [ ] 后端正常运行下，基线记录：`systemd-run --user --collect --pipe redis-cli -a redis_linchat_123 -n 0 INFO clients | grep connected_clients`
- [ ] 触发 20 次会命中 get_redis 的请求（如多模态/语音/rate_limit 路径）
- [ ] 再次读 `connected_clients`，DB0 峰值应显著低于改造前，目标 ≤ 30
- [ ] 确认 DB3（Channels）`redis-cli -n 3 INFO clients` 不受影响（数值与改造前一致）

### 5.3 性能验证（P1）
- [ ] 对比 connected_clients（DB0）峰值：预期减少 50%+（04-plan metrics）
- [ ] 无现成 `measure-voice-latency` 关联指标要求；本 batch 以连接数为主指标

### 5.4 回归验证
- [ ] `pytest backend/tests/common/ backend/tests/chat/ backend/tests/graph/ backend/tests/voice/ -v`（覆盖全部 get_redis 调用方所属域）
- [ ] 重点确认 event_service（pubsub）、cancel_monitor、voice_session_service（含 .aclose()）用例全绿

## 6. 回滚策略

来自 04-refactor-plan.json：`git revert <commit>`；恢复每次新建连接模式。

具体操作：
```bash
# 单 commit revert（本 batch 应为单一 commit）
git revert <commit-hash>
# 或 worktree 整批撤销
git worktree remove ../linchat-batch-11
git branch -D refactor/batch-11
```
回滚零风险：调用点签名与 §3 证明的 `.aclose()` 语义两侧一致，revert 后行为完全回到 HEAD=a9d1305。

## 7. 风险点

1. **连接池容量 vs pubsub 长连接（最大风险）**：默认 `ConnectionPool` 超过 `max_connections` 时**抛 `ConnectionError("Too many connections")` 而非阻塞**。每个活跃 SSE 订阅 + 每个进行中的 cancel_monitor 各长期占用 1 条连接。若并发订阅数 + 并发命令数逼近 50，会报错。家庭场景并发极低，50 足够；但需安琳确认上限值（见待决策）。
2. **事件循环隔离**：已用 WeakKeyDictionary+get_running_loop 处理；uvicorn 单 loop 无影响，pytest 多 loop 各自建池、loop 回收自动清理。
3. **decode_responses/encoding 一致性**：池参数与原 `from_url` 完全一致，返回值仍为 str（非 bytes），调用方无感知。
4. **`.aclose()` 语义变化**：已证明为安全（仅归还连接）；但需回归 voice_session_service / response_decision_service 用例确认。

## 8. 需要安琳确认的事项

- [ ] **max_connections 取值**：默认拟设 50（对齐 CACHES）。是否认可？家庭多用户场景峰值 SSE 订阅数约几个，命令并发低，50 有充足冗余；若担心，可提到 100。是否需要改用 `BlockingConnectionPool`（超限时阻塞等待而非报错）？默认方案用普通 `ConnectionPool`（超限报错，语义更早暴露问题）。
- [ ] **settings.py 486 行 > 300 硬限制**：本 batch 仅追加 2 行配置，不触及其结构。**建议不在本 batch 拆分**（拆 settings.py 属独立高风险重构，会大幅扩 scope）。是否同意维持现状、仅追加？
- [ ] **`timedelta` import 清理**：执行时会 `rg "timedelta" backend/core/redis.py` 确认是否仍被引用；若无引用则删除该 import（顺带精简），有则保留。是否授权此顺带清理？
- 除以上外：scope 与 04-plan 一致（3 文件），无跨 do_not_touch，无新依赖（redis-py 已装 7.1.0），Channels/Docker 拓扑不变，隔离粒度仍 user_id。**无其他阻塞。**

## 9. 新增测试清单（backend/tests/test_redis.py）

全部用真实 aioredis 对象 + monkeypatch 校验池语义，不依赖真实 Redis 网络（用 fakeredis 或 mock ConnectionPool；优先 monkeypatch `ConnectionPool.from_url` 返回可探测对象）：

- [ ] `test_get_redis_reuses_pool_same_loop`：同一 loop 内两次 `get_redis()` 返回的 client 共享同一 `connection_pool` 对象（`is` 相等）。
- [ ] `test_pool_isolated_per_event_loop`：`asyncio.run` 两个独立 loop 各自 `_get_pool()`，池对象不同，且第一个 loop 结束后 `_POOLS` 中该条目被 GC（WeakKeyDictionary 自动清理）。
- [ ] `test_aclose_does_not_disconnect_shared_pool`：client 构造自共享池，调用 `.aclose()` 后再 `get_redis()` 仍能取到同一存活池（`auto_close_connection_pool is False` 断言）。
- [ ] `test_max_connections_from_settings`：override `settings.REDIS_MAX_CONNECTIONS=7`，`_get_pool().max_connections == 7`。
- [ ] `test_get_async_redis_client_alias_uses_pool`：别名函数返回的 client 同样绑定共享池。
- [ ] `test_get_redis_returns_str_decode_responses`（可选，需 fakeredis）：set/get 往返得到 str，验证 decode_responses 保留。

## 10. 执行预算

- 预计 tool calls：15-25（读 3 文件 + 编辑 3 文件 + 若干次 pytest/ruff 验证）
- 预计 token：中等（单模块 + 单测试文件）
- 预计时间：1 session，符合 estimated_sessions=1。无需拆分。
