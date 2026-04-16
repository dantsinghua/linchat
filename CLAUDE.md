# LinChat — Claude Code 协作契约

> 本文件是 Claude 在本项目工作时的**核心契约**，不可跳过。
> 详细信息见 `docs/` 目录，按需加载，不要预读全部。
> 保持精简是原则：本文件任何时候不应超过 120 行。

---

## 一句话项目定位

LinChat 是一个企业级多模态 AI Agent 聊天应用（家庭场景），Django 4.2 + Next.js 14，
后端 ~13k LOC / 前端 ~13k LOC / 1462+ 测试函数，公网 `https://www.greydan.xin/linchat`。

## 当前工作阶段（由我在对话开头声明；默认 Phase 0）

- **Phase 0**：日常开发（默认）
- **Phase 1**：只读重构分析 — 禁止修改 `backend/`、`frontend/` 下任何业务代码，产出只写入 `refactor/`
- **Phase 2**：批次执行重构 — 严格按 `refactor/04-refactor-plan.json` 中的单个 batch 执行

## 文档导航（按需加载）

| 场景 | 文档 |
|------|------|
| 系统架构、数据流、数据模型 | `docs/architecture.md` |
| **历史包袱与已知痛点** | `docs/legacy-and-debts.md` ★ 重构必读 |
| 网络/端口/Nginx/frpc | `docs/infrastructure.md` |
| 部署与服务启动 | `docs/deployment-guide.md` |
| 编码规范 | `docs/code-standards.md` + `.specify/memory/constitution.md` |
| 测试指南 | `docs/testing-guide.md` |
| API 参考 | `docs/api-reference.md` |
| 配置参考 | `docs/configuration-guide.md` |

---

## 绝对红线（违反即任务失败）

### 环境类

1. **必须先激活虚拟环境**：`source /home/dantsinghua/work/linchat/linchat/bin/activate`
2. **后端必须用 uvicorn ASGI**：`uvicorn core.asgi:application --host 0.0.0.0 --port 8002`；禁用 `runserver`
3. **前端走生产模式**：`npm run build` + `npm run start -- -p 3784`；禁用 `npm run dev`
4. **应用服务管理走 `./scripts/services.sh {start|stop|restart|status}`**，禁止手动 nohup（会产生孤儿进程）

### 架构类

5. 分层顺序：`views.py` → `services/` → `repositories.py`，**视图层禁止写业务逻辑**
6. PostgreSQL 是唯一可信数据源，ES/Redis 是副本，写操作必须原子（失败必须回滚）
7. **所有隔离粒度永远为 `user_id`**，禁止引入 `conversation_id` / `session_id` 字段或概念
8. LLM 异常必须分类处理：`LLMConnectionError`、`LLMTimeoutError`、`LLMRateLimitError`、`LLMContentFilterError`
9. SSE 视图必须用 ASGI 原生异步，禁止手动创建临时事件循环

### 安全类

10. Token 必须 httpOnly Cookie，禁止 localStorage
11. 密码必须 SM3 哈希，API 密钥必须 SM4 加密
12. 敏感信息（`.env*`、密钥、证书）禁止提交版本控制

### 质量类

13. 禁止裸 SQL，必须用 ORM
14. 禁止跳过测试直接部署
15. 覆盖率要求：总体 ≥ 80%，关键路径 ≥ 95%，服务层 ≥ 95%

---

## Do Not Touch 路径（需专项流程才能改）

- `.specify/memory/constitution.md`（项目宪法）
- 任何 `*.sql` 已执行的 migration 文件
- `.env*`、证书、密钥文件
- 生产配置文件

---

## 强制术语（不可违背）

| 术语 | 定义 |
|------|------|
| **1 轮对话** | 1 条 role=user + 1 条 role=assistant 消息 |
| **保留最近 N 轮** | 保留最后 N×2 条 user/assistant 消息 |
| **隔离粒度** | 永远按 `user_id`，不存在 session/conversation 粒度 |
| **单用户单会话** | 一个用户对应一个会话，Message 只有 `user_id`，没有 conversation_id |

---

## 性能指标（超标视为退化）

| 场景 | 指标 |
|------|------|
| API GET | p95 < 200ms |
| API POST | p95 < 300ms |
| 大模型首 token | < 2s |
| 前端 FCP | < 1.5s |
| 前端打包体积 | < 200KB (gzip) |

---

## 上下文效率原则（给 Claude 的工作方式约束）

- 先 `rg` / `glob` 定位，再精读；禁止盲目全读 >500 行文件
- 读大文件先抓结构：`rg "^(def|class|async def) " <file>`
- 复杂多文件任务必须委派给 `.claude/agents/` 下的子代理
- Phase 1 分析产出一律写入 `refactor/` 目录，不在主对话里堆砌
- 每轮完成后在 `refactor/claude-progress.txt` 更新进度，便于跨 session 恢复

---

## 不确定时必须提问（不要自行决策）

遇到以下情况**停止并问我**：

- 需要跨 Do Not Touch 边界
- 需要改动数据库 schema 或 migration
- 需要引入新第三方依赖或中间件
- 对外 API 契约（REST/SSE/WebSocket）变更
- 性能数字无法从代码判断（需要压测验证）
- 触碰 `docs/legacy-and-debts.md` 中标记为"没人敢动"的区域

---

## 提交规范

`<type>(<scope>): <description>`，type ∈ `feat | fix | refactor | docs | style | perf | test | chore`
示例：`refactor(graph): 提取 AgentService 中的工具调用编排逻辑`

---

*本文件随项目演进持续更新，与 `.specify/memory/constitution.md` 同步。*
