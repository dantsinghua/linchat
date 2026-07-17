# wn-linchat-brain v3 · Phase B+C 设计文档

> 状态：**待安琳 review**　|　日期：2026-07-18　|　作者：Claude（主 agent）
> 前置：Phase A（公众号 FTS5 知识底座 `oa_indexer.py` / `oa_fts.db` 3591 篇）已交付
> 事实依据：三份只读探查报告（probe-graph / probe-memory-auth / probe-wechat），全部证据锚定 `文件:行`

---

## 0. 背景与目标

把"微信外脑"两个内容源接进 LinChat 的 LangGraph agent，让 **AI 老公成为与 Web、语音同级的第三条 channel**，共享同一套 `user_id=7` 记忆/上下文：

- **知识面** = 公众号（we-mp-rss 3591 篇）→ FTS5 检索 → agent 工具 `oa_search`（Phase A 已建索引，本期只做接入）
- **记忆面** = 私聊/群聊对话 → 落库 → 日合成要点 → 摄入 LinChat memory（`type=wechat`，`user_id=7`）

出站按来源 channel 路由：**微信来的才发回微信；Web/语音来源绝不回灌微信**（防串台，高危红线）。

---

## 1. 范围与决策记录

### 1.1 本期范围（4 个 batch）

| batch | 触碰代码库 | 一句话 |
|---|---|---|
| **B1** | wechat-narrator（旁路，零 LinChat 风险）| 对话 tee 落独立 SQLite + 历史回填 + 日合成 timer |
| **B2** | LinChat 生产 + wechat | 内部摄入端点（设备 token）+ wechat 侧摄入客户端 |
| **C1** | LinChat 生产 | `oa_search` 工具接入 graph + `channel` 参数透传 |
| **C2** | LinChat 生产 + wechat | 老公 channel 非流式端点 + auto_reply 改调 agent + 出站防串台 |

依赖关系：**B1 与 C1 无依赖、可并行**（不同库）；**B2 依赖 B1**（需 messages 库数据做端到端摄入）；**C2 依赖 C1（channel 透传）+ B2（摄入端点/鉴权模式）**。推荐执行序：`B1 ∥ C1 → B2 → C2`。

### 1.2 决策记录（安琳 2026-07-18 拍板）

| # | 决策 | 结论 | 依据 |
|---|---|---|---|
| Q1 | embedding 私有化 | **保持云端 OpenAI**，不切 qwen3，砍掉原 B0 | 实测连通 OK；公众号走 FTS 不 embed，仅对话日合成要点 embed；$100 够几十年 |
| Q2 | 摄入 memory 的 type | **新增 `wechat`/`oa` 枚举** | 来源可区分、前端可过滤、避免 daily-summary 误抓真实对话记忆 |
| Q3 | 老公→LinChat 接口形态 | **新增非流式内部端点**（服务端聚合 SSE 成整句返回）| auto_reply 是同步阻塞 `urlopen`，与主链路 SSE 流式协议不匹配 |
| Q4 | 老公人设 PERSONA 归属 | **交给 LinChat agent 系统 prompt**；本地 PERSONA 退为降级兜底 | 三 channel 人设/记忆统一，避免双重人设冲突 |
| — | 老公 channel 鉴权 | **复用设备 token**（`RegisteredDevice` + SM4）| 已有机制，隔离粒度天然是 user_id |
| — | 老公 channel thread 隔离 | **独立 `thread_id="user_7_wechat"`** | 对话线程隔离防并发交错；长期 memory 仍按 user_7 共享 |

---

## 2. 架构总览

### 2.1 三库 + 四 channel

```
                      ┌─────────────────── LinChat (Django ASGI, :8002) ───────────────────┐
  Web ────────────────┤ ChatService ──┐                                                     │
  语音 reSpeaker ─────┤ voice_pipeline ┼─→ AgentService.execute(user_id=7, channel=...) ──→ LangGraph │
  微信老公(新) ───────┤ 老公端点(新)   ┘        │                    ├─ tools: oa_search(新) │
                      │                         │                    └─ memory(共享 user_id=7)│
                      │  内部摄入端点(新) ──→ MemoryService.create_memory(type=wechat)        │
                      └──────────────────────────────────────────────────────────────────────┘
                            ▲ 设备token鉴权                    ▲ oa_search 只读 ATTACH
  ┌───── wechat-narrator (旁路) ─────┐                    ┌──── oa_fts.db (Phase A, 3591篇) ────┐
  │ group2email tee ─→ messages.db   │─ msg_synth 日合成 ─→ linchat_ingest_client ─→ 摄入端点
  │ auto_reply.generate_reply ───────┼─→ 老公端点(失败降级 _openclaw)
  └──────────────────────────────────┘
  we-mp-rss(docker :8001) ─→ oa_indexer build ─→ oa_fts.db   (Phase A 已完成，本期不改)
```

### 2.2 channel 语义（新增，贯穿 C1/C2）

当前 LinChat **完全没有 channel/source 概念**（probe-graph B3：web/voice 的 `thread_id` 都是 `user_{id}`，无从区分来源）。这正是"出站防串台"必须先解决的根因。

新增 `channel: str` 参数，取值 `web` / `voice` / `wechat`，从入站端点一路透传到 `AgentService.execute` → `get_agent_config`，并注入 Langfuse tag `channel:*`。**出站路由的唯一判据就是这个 channel**。

---

## 3. Batch 分解与改动清单

### B1 — 对话落库（wechat-narrator 旁路，零 LinChat 风险）

**目标**：把群/私聊对话 tee 一份到独立 SQLite，供 B2 日合成摄入。失败降级只 log 不影响转发主流程。

**B1.1 独立 messages 库**（新建 `~/clawd/scripts/wechat-narrator/messages_store.py`）
- 独立 SQLite `messages.db`，`PRAGMA busy_timeout`，与 we-mp-rss 主库物理隔离
- 表 `messages`：
  ```sql
  CREATE TABLE IF NOT EXISTS messages(
    id INTEGER PRIMARY KEY,
    session TEXT,            -- 会话/群名 (who)
    kind TEXT,              -- '群' | '私信'
    sender TEXT,            -- 'self'|'peer'|发言人名|'unknown'
    msg_type TEXT,          -- 'text'|'image'|'audio'
    text TEXT,
    assets TEXT,            -- JSON: 图片/语音资产路径
    ts INTEGER,             -- 消息墙钟时间
    content_hash TEXT UNIQUE, -- 幂等去重 (session+sender+text+ts 哈希)
    ingested INTEGER DEFAULT 0, -- 是否已被 msg_synth 消费
    created_at INTEGER
  );
  ```
- 幂等：`content_hash` UNIQUE + `INSERT OR IGNORE`
- 所有写库操作 `try/except`，异常仅 `log()`（仿 `wechat_group_to_email.py:59` 风格），绝不 raise

**B1.2 tee 注入点**（改 `wechat_group_to_email.py`，运行中脚本 → 改前二次确认）
- 群消息：`:361`（单条 `send_mail` 路径）+ `:344`（洪峰路径），此处已有 `who/text/kind/attachments`
- 私聊（灰灰）：`:288-309` 之间，此处能拿 `peer_new`（结构化逐条 `{kind,text,sender}`）+ AI 回复 `r["reply"]`，成对落库
- 注入 = 调 `messages_store.tee(...)`，~10 行 try/except

**B1.3 历史回填**（新建 `backfill_messages.py`，一次性）
- 解析 narrator/group2email 日志，补历史对话进 messages 库（幂等，可重复跑）

**B1.4 日合成**（新建 `msg_synth.py` + systemd user timer）
- 每日定时：读 messages 库 `ingested=0` 的记录 → 调 LLM 合成"今日对话要点"（≤10000 字，符合 `MEMORY_CONTENT_MAX_LENGTH`）→ 交 `linchat_ingest_client`（B2）摄入 → 成功后标 `ingested=1`
- LLM 复用现有 `_openclaw` 网关或 DashScope

**门禁**：wechat 侧 pytest（tee 幂等、私聊路由、pending 消费）。**不碰 LinChat，不跑 validate_full**。

---

### B2 — 摄入通道（LinChat 生产 + wechat）

**目标**：LinChat 侧开一条内部摄入路径（带 type/tag，不改对外 API 契约）；wechat 侧写摄入客户端（幂等 + pending 队列，LinChat 宕机不丢）。

**B2.1 新增 `wechat`/`oa` memory type**（改 `apps/memory/models.py:10-14`）
- `UserMemory.MemoryType` TextChoices 加 `WECHAT="wechat"`、`OA="oa"`（CharField，**无 migration**，probe-memory C1）
- 检查 daily-summary/monthly-summary 的数据源过滤（`apps/memory/services.py` + `tasks.py`），确保新 type **不被误抓进对话总结**（关键，需在 batch 内核实并加测试）

**B2.2 内部摄入端点**（新建 view + url）
- 路径如 `POST /api/v1/internal/ingest/`，加入 `PUBLIC_PATHS`（`apps/common/middleware.py:18`）跳过 cookie 中间件（probe-memory D4-a）
- header `X-Device-Token` 带设备 token → view 内 `async_to_sync(device_service.authenticate_by_token)(token)` 拿 `user_id`（复用现成逻辑，一行不改鉴权核心）
- 鉴权后 `async_to_sync(MemoryService.create_memory)(user_id=..., content=..., name=..., type="wechat", tag=...)`
- ⚠️ **embedding 门禁坑**（probe-memory C3）：`generate_embedding` celery task 有 `has_active_users()` 门禁，有人在线就 skip。摄入端点内**直接同步调 `EmbeddingClient.generate_embedding`** 生成向量，不依赖 `.delay()`，保证实时入向量库
- 幂等：content 哈希去重（DB 查重或依赖 messages 库 `ingested` 标记）

**B2.3 摄入客户端**（新建 `~/clawd/scripts/wechat-narrator/linchat_ingest_client.py`）
- 持设备 token，POST 摄入端点；重试 + 幂等（content 哈希）+ 本地 pending 队列（LinChat 宕机时暂存，恢复后补投）

**门禁**：`validate_full.sh` 全绿（基线 1772 passed）+ wechat 侧 pytest（pending 补投、GreenMail）。

---

### C1 — oa_search 工具 + channel 透传（LinChat 生产）

**目标**：公众号 FTS 检索作为 agent 工具；channel 参数贯穿，为 C2 出站路由铺路。

**C1.1 `oa_search` 工具**（新建 `apps/graph/tools/oa_search.py`，仿 `history.py`）
- `@tool async def oa_search(query: str, config: RunnableConfig, limit: int = 5) -> str`
- docstring 描述"公众号知识库检索"（LLM 可见）
- 内部：只读 `sqlite3.connect("file:{OA_FTS_DB}?mode=ro", uri=True)` + FTS5 MATCH（复用 `oa_indexer.search` 逻辑）
- ⚠️ **异步**：sqlite 是阻塞 IO，用 `asyncio.to_thread(...)` 包裹，别卡事件循环（probe-graph C1）
- ⚠️ **截断**：`return cap_tool_result(text, "oa_search")`（FTS 命中易超 token，probe-graph A4）
- 查无命中返回明确"未查到"（防幻觉红线）
- 末尾 `OA_TOOLS = [oa_search]`

**C1.2 注册**（改 `apps/graph/subagents/__init__.py:52-54`）
- 仿 `history_search`，`tools.append(oa_search)`，加 `if getattr(settings,"OA_SEARCH_ENABLED",False):` 开关
- settings 加 `OA_SEARCH_DB_PATH`（指向 `oa_fts.db`）、`OA_SEARCH_ENABLED`
- 同步 `apps/graph/graph.py:50-52` 离线 chat_graph（保守，防漏）

**C1.3 channel 透传**（改 4~5 处，probe-graph C2）
- `apps/graph/services/agent_service.py:34`：`execute(...)` 签名加 `channel: str = "web"`
- `agent_service.py:81`：`get_agent_config(user_id, [...], channel=channel)`
- `apps/graph/agent.py:138`：`get_agent_config` 加 `channel` 参数，函数体加 `config["metadata"]={"langfuse_tags":[f"channel:{channel}"],"channel":channel}` + `config["configurable"]["channel"]=channel`
- `apps/chat/services/chat_service.py:43`：调用加 `channel="web"`
- `apps/voice/services/voice_pipeline.py:153`：调用加 `channel="voice"`

**门禁**：`validate_full.sh` 全绿。E2E 冒烟：Web 对话触发 oa_search 召回带引用。

---

### C2 — 老公 channel（LinChat 生产 + wechat，最高危）

**目标**：微信老公改调 LinChat agent（共享记忆/人设），失败降级；**出站严格按 channel 路由防串台**。

**C2.1 非流式老公端点**（新建 view + url，Q3）
- `POST /api/v1/internal/husband/reply/`，加入 `PUBLIC_PATHS` + 设备 token 鉴权（同 B2.2）
- 请求体：`{message, channel:"wechat", origin_peer:"灰灰", image?}`；服务端调 `AgentService.execute(user_id=7, channel="wechat", thread_id="user_7_wechat", ...)`，**聚合 SSE 流为完整整句**后一次性返回
- 响应体：`{reply, channel:"wechat", origin_peer:"灰灰"}` —— **原样回带 channel+origin_peer 作回声令牌**（防串台层1）
- **独立 thread_id `user_7_wechat`**：微信对话线程与 Web/语音物理隔离（防串台层3）；长期 memory 仍按 user_7 共享
- 图片入参：确认接受 base64 image_url / attachment_uuids（对齐 auto_reply 现有多模态喂法）
- 人设：**不在此注入 PERSONA**（Q4，交给 agent 系统 prompt）

**C2.2 auto_reply 改造**（改 `wechat_auto_reply.py:220 generate_reply`，probe-wechat G4）
- `generate_reply` 里先 POST 老公端点；`try/except`（超时/非200/空回复）失败则 fallback 到现有 `_openclaw(...)`（本地 PERSONA 兜底）
- **不动**：双闸门 `send_reply:256-281`、会话状态机、游标、图片截图、颜色发言人判定
- 人设：本地 PERSONA 仅在降级路径使用

**C2.3 出站防串台（三层防护，红线高危）**
微信发送**只由 auto_reply 触发**（wechat-narrator 进程内），Web/语音物理上不进此进程。针对三个真实风险层次分层防护：
- **层1 · 回声令牌闭环**（治"回复错配"）：auto_reply 调端点带 `channel="wechat"+origin_peer`，端点原样回带；auto_reply 校验 `channel==wechat && origin_peer==当前会话` 才发，否则丢弃（~15 行）
- **层2 · 发送侧双闸门**（治"发错会话"，已存在 0 改动）：`send_reply:256-281` 的 `title==peer` 物理硬闸，宁可不发绝不发错
- **层3 · 独立 thread**（治"并发交错"）：老公 channel `thread_id="user_7_wechat"`，对话线程与 Web/语音物理隔离；长期 memory 仍按 user_7 共享
- 覆盖大部分边缘场景；极端情况（如伪造回声令牌）由设备 token 鉴权门槛兜底，不额外加签名以保持简洁

**门禁**：`validate_full.sh` 全绿 + **三服务联测 E2E**（见 §5.3）。

---

## 4. 关键坑与防护（务必守）

| 坑 | 来源 | 防护 |
|---|---|---|
| `has_active_users()` 门禁：有人在线跳过所有 celery embedding | probe-memory C3 | 摄入直接同步调 `EmbeddingClient`，不走 `.delay()` |
| 出站串台（发错会话/回复错配/并发交错）| probe-graph B3 | 三层：回声令牌 + 双闸门 + 独立 thread（详见 C2.3）|
| SSE 流式 vs auto_reply 同步阻塞 | probe-wechat G4 | 新增非流式端点服务端聚合 |
| 双重人设冲突 | Q4 | 人设只在 agent 系统 prompt；本地 PERSONA 退兜底 |
| `wechat-narrator.service` restart 会清登录态强制重扫码 | probe-wechat H4 | 测试**绝不** restart 该服务；重启 group2email 用 kill 子进程让 supervisor 重拉 |
| 发言人判定绑定固定窗口坐标（1280x720）| probe-wechat F3/H4 | 测试勿动 :99 窗口几何 |
| UI 操作互斥（截图/转写占用 3-4s） | probe-wechat H4 | E2E 串行触发，避免并发 UI 争用 |
| `oa_search` 阻塞 IO 卡事件循环 | probe-graph C1 | `asyncio.to_thread` 包裹 |
| 新 type 被 daily-summary 误抓 | probe-memory C1 | B2.1 核实过滤 + 加测试 |
| 隔离粒度红线 | CLAUDE.md #7 | 全程 user_id，无 conversation_id/session_id |
| PostgreSQL 唯一可信源 | CLAUDE.md #6 | oa_fts.db 是外部只读副本（纯检索、不写、无 migration）；messages.db 属 wechat 侧独立库不受 ORM 红线约束 |

---

## 5. 测试方案

### 5.1 单元/集成（每 batch 门禁）
- **B1**：wechat pytest — tee 幂等（同一消息不重复入库）、私聊/群路由、pending 消费、写库失败降级不影响转发
- **B2**：LinChat pytest — 摄入端点鉴权（设备 token 有效/无效）、`create_memory(type=wechat)` 落库、embedding 同步生成、daily-summary 不抓 wechat type；wechat pytest — 摄入客户端幂等 + pending 补投（GreenMail/mock）
- **C1**：graph pytest — oa_search 被调用、带公众号引用、查无命中不幻觉、cap_tool_result 截断；channel 参数透传到 config + Langfuse tag
- **C2**：graph pytest — 老公端点聚合回复、channel="wechat" 标识；**channel 路由**：微信来源发回微信 / Web 来源不发微信 / 三 channel 共享 user_id=7 记忆

### 5.2 全量回归
每个碰 LinChat 的 batch（B2/C1/C2）合并前后跑 `refactor/loop/validate_full.sh`，基线 **1772 passed**，必须全绿。

### 5.3 三服务联测 E2E（C2 收尾，安琳明确要求）
测试子代理组织，**只读优先、绝不 restart wechat-narrator**：
1. **前置确认**（只读）：`systemctl --user status wechat-narrator/wemprss-bridge/wn-login-sentinel`；`docker ps` we-mp-rss + greenmail；微信已登录（`tail statemachine.log` / :8899）；LinChat `./scripts/services.sh status`
2. **知识面**：Web/微信问一个公众号话题 → oa_search 召回带引用（linchat-login 技能 + 真机）
3. **记忆面**：微信私聊 → tee 落库 → msg_synth 合成 → 摄入 memory(type=wechat) → 另一 channel 能召回
4. **channel 路由**：微信来源回复发回微信（经双闸门）；Web 来源**不发**微信；三 channel 记忆互通
5. **降级**：老公端点不可达 → auto_reply fallback `_openclaw`，回复仍发出

---

## 6. harness 自动化流程

每个 batch 走既有 refactor/loop 纪律：
```
batch-initializer（读改动清单，深研文件，出执行计划）
      ↓ 安琳 review
batch-executor（改代码 + lint + 局部测试 + commit/push 到 batch 分支）
      ↓
batch-validator（validate_full.sh 全量 + 手动验证清单 + SLO 对比 → COMPLETED / ROLLBACK）
      ↓ 全绿
下一个 batch
```
- 主 agent（我）监督调度 + 门禁把关 + 阻塞上报
- **Git 操作**：batch 分支自动流程内的 commit/push 由 batch-executor 执行（符合"batch 自动流程除外"）；其余 git 操作给命令交安琳
- 最后测试子代理组织 §5.3 三服务联测

---

## 7. 回滚策略

- 每 batch 独立分支，`validate_full` 失败 → `batch-validator` 判 ROLLBACK，回退分支
- B1/B2 wechat 侧改动：tee/摄入客户端全 try/except 降级，出错自动退回"仅转发"原行为
- C2 老公端点：auto_reply 有 `_openclaw` 降级兜底，端点故障不影响微信回复
- `OA_SEARCH_ENABLED` / 老公端点均可配置开关，灰度关闭即回退

---

## 8. 红线合规检查

- ✅ 隔离粒度全程 user_id（无 conversation_id/session_id）
- ✅ 不改对外 API 契约（摄入走内部端点，`MemoryCreateSerializer` 不动）
- ✅ 不碰 schema migration（新 type 是 CharField 枚举值；oa_fts/messages 是 wechat 侧独立 SQLite）
- ✅ 不碰 SSE 核心/SM4/LangGraph 版本/Docker 拓扑/前端栈/Gateway 契约
- ✅ LinChat 侧不裸 SQL（走 ORM）；FTS 属 wechat 侧独立库
- ✅ Token 走设备 token（SM4），不引入 localStorage
- ⚠️ 改运行中的 `wechat_group_to_email.py` / `wechat_auto_reply.py` → 改前二次确认（B1.2 / C2.2）

---

*本设计文档为 wn-linchat-brain v3 Phase B+C 的 source of truth，随执行推进更新。*
