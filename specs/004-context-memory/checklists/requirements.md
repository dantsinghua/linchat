# 需求检查清单 — M1b 上下文与记忆管理

**特性**：004-context-memory
**日期**：2026-01-31（v3.0 全面更新）
**验证日期**：2026-02-02（代码实现验证通过）

> 交叉引用：[spec.md](../spec.md) | [data-model.md](../data-model.md) | [process-model.md](../process-model.md) | [behavior-model.md](../behavior-model.md) | [rule-model.md](../rule-model.md) | [plan.md](../plan.md)

---

## 一、功能完整性

### 1.1 分层上下文组装（FR-001, FR-002, FR-014）

- [X] PromptBuilder 实现 6 个核心 build 方法
- [X] build_system_prompt() — 层级 1，基础角色 + 行为规范 + 功能模块（~2k tokens）
- [X] build_template_block() — 层级 2.a，prompt 模板固定部分（~1k tokens）（注：内容合并到 build_system_prompt 中，含输出格式规范和行为指引）
- [X] build_memory_block() — 层级 2.b，召回记忆注入为独立 system 消息
- [X] build_tool_context() — 层级 2.c，工具定义注入
- [X] build_conversation_history() — 层级 2.d，短期对话历史
- [X] build_messages() / build_messages_for_langchain() — 最终消息列表
- [X] PromptModule 枚举实现（BASE/REASONING/TOOL_USAGE/CODE_ASSIST/CREATIVE_WRITING/DATA_ANALYSIS）
- [X] register_custom_module() 运行时动态扩展
- [X] 有效上下文窗口 = max_context_window × 0.9（R-001）
- [X] Token 计数：tiktoken cl100k_base 编码（R-017）
- [X] 模型上下文窗口 < 10,000 tokens 时拒绝使用
- [X] 每段加载后计算总 token 数，超限触发压缩

### 1.2 优先级驱动的上下文压缩（FR-003, FR-004, FR-015）

- [X] 压缩顺序：前对话(2.d) → 工具内容(2.c) → 记忆内容(2.b)
- [X] 上下文工具集实现：contextCompact / contextExtract / contextPrune
- [X] 用户当前输入(2.e)永远不压缩
- [X] d → c → b 全部处理后仍超限时直接截断
- [X] 并发控制：Redis 分布式锁（key=compress:{user_id}）
- [X] 未获锁的请求等待后重新检查 token
- [X] 10% buffer 预留，超过 100% 直接截断（安全兜底 R-019）
- [X] LLM 压缩失败重试 3 次后回退简单截断（R-014）
- [X] 回退截断不生成 compaction 记忆
- [X] 压缩过程对话不中断

### 1.3 记忆 CRUD（FR-008, FR-009, FR-011）

- [X] 创建记忆（REST API type 固定为 memory）
- [X] 读取记忆（列表 + 详情）
- [X] 更新记忆（content 更新 + embedding 重新生成）
- [X] 删除记忆（级联删除 embedding，FK CASCADE）
- [X] content 最大 10,000 字符，超出由序列化器拒绝（R-021）
- [X] user_id 由视图层从 request.user.user_id 注入
- [X] API 不接受客户端传入 user_id 和 type
- [X] 创建时 embedding_status = pending, retry_count = 0

### 1.4 记忆工具集（FR-005）

- [X] memSearch — 混合检索（向量 0.7 + 关键词 0.3 加权）
- [X] memCache — 写入 user_memory 表
- [X] memUpdate — 更新一至多个记忆
- [X] memDelete — 删除一至多个记忆

### 1.5 语义搜索与混合检索（FR-010）

- [X] pgvector 向量检索（CosineDistance）
- [X] PostgreSQL 全文检索（tsvector + GIN + pg_jieba 中文分词）
- [X] 混合检索最多返回 5 条结果
- [X] 向量相似度权重 0.7 / 关键词匹配权重 0.3
- [X] embedding_status != done 的记录退化为关键词匹配（R-005）
- [X] 对话前自动召回相关记忆（retrieve_relevant_memories）
- [X] 召回记忆注入位置：system prompt 和模板之后、工具内容之前（层级 2.b）

### 1.6 Embedding 异步生成与同步（FR-011）

- [X] 异步生成 embedding（Celery 任务）
- [X] 状态流转：pending → processing → done / failed（R-005）
- [X] 向量维度固定 2048，写入时校验（R-011）
- [X] content token 超出模型限制时截取前 N tokens
- [X] 从 model 表获取 type='embedding' 配置
- [X] 无 embedding 配置时抛出 EmbeddingConfigNotFoundError（R-011）
- [X] API Key SM4 解密后使用（注：解密在 model_service.get_active_model 上层处理）
- [X] 失败后 retry_count += 1（R-013）
- [X] 超过 3 次重试永久标记 failed，退化为关键词匹配
- [X] 定时扫描 failed/pending 超时记录，自动重试（每 5 分钟）
- [X] 更新时旧 embedding 标记失效 → 新 embedding 写入 → 删除旧数据
- [X] Embedding 服务不可用时元数据正常写入不阻塞（R-015）

### 1.7 记忆总结（FR-012, FR-013）

- [X] 三种触发方式：主动压缩(compaction)、每日定时(daily-summary)、每月定时(monthly-summary)
- [X] 共用核心方法 summarize_and_store
- [X] 数据来源降级策略：压缩记忆 → message 表原始对话 → 跳过（R-007）
- [X] 活跃用户定义正确（每日/每月各有明确条件）
- [X] cronMem 流程使用专用 prompt（参考 mem0 设计）
- [X] cronMem 输出：content、tags、更新 date
- [X] cronMem LLM 调用失败：重试 3 次后跳过（R-022）
- [X] 无数据时不生成空总结

### 1.8 LangGraph 四流程编排（FR-007）

- [X] chat 流程：完整上下文 + 记忆工具 + python repl + bravo search + home assistant
- [X] context 流程：(1)+2.a+2.e+对应内容 + 仅上下文工具
- [X] memory 流程：(1)+2.a+2.e+2.b + 仅记忆工具
- [X] cronMem 流程：专用 prompt + 无工具（Agent → End）
- [X] 各流程工具集严格隔离，不越界（R-018）
- [X] context/memory 流程超长输入直接截断
- [X] 串行前置模式：上下文超限 → context → memory → chat

### 1.9 专用 Prompt 模板（FR-014）

- [X] COMPACTION_PROMPT_TEMPLATE — 对话压缩摘要
- [X] DAILY_SUMMARY_PROMPT_TEMPLATE — 每日记忆总结
- [X] MONTHLY_SUMMARY_PROMPT_TEMPLATE — 每月记忆总结
- [X] CRONMEM_PROMPT_TEMPLATE — 定时事实抽取与打标（参考 mem0）

### 1.10 前端上下文压缩状态提示（FR-016）

- [X] SSE 事件 context_compacting（开始）/ context_compacted（完成）
- [X] 复用现有对话 SSE 流，不开设独立通道
- [X] 前端对话框左下角显示/隐藏"正在压缩上下文"状态标识
- [X] chatStore 新增 isCompacting 状态
- [X] useChatStream 处理压缩事件

---

## 二、安全与隔离

- [X] 所有记忆查询强制 user_id 过滤（R-004）
- [X] 无 user_id 查询抛出异常
- [X] 用户 A 记忆不可被用户 B 访问
- [X] 隔离测试用例完整（test_isolation.py）
- [X] 并发压缩 Redis 分布式锁（user_id 粒度）
- [X] API Key SM4 加密存储
- [X] user_id 来源：视图层 request.user.user_id，不接受客户端传入

---

## 三、数据一致性

- [X] 记忆创建/更新事务保护（R-009）
- [X] 两表最终一致性保证（R-006）
- [X] 删除级联（FK ON DELETE CASCADE）
- [X] 并发操作一致性
- [X] 定时扫描修复不一致记录

---

## 四、性能

- [X] 语义搜索延迟 < 500ms（R-010）
- [X] 上下文裁剪/压缩额外延迟 < 500ms（不含 LLM 压缩摘要等待时间）
- [X] Token 计数本地计算（tiktoken，延迟可忽略）
- [X] 混合检索结果去重、排序效率

---

## 五、可靠性

- [X] 任何上下文大小下对话不中断（NFR-005）
- [X] 压缩/截断操作有兜底策略
- [X] 不因超出模型上下文窗口而报错终止
- [X] 压缩 LLM 调用失败回退到简单截断（R-014）
- [X] cronMem LLM 调用失败跳过用户，下次重试（R-022）
- [X] Embedding 服务不可用时记忆功能降级但不中断（R-015）

---

## 六、可观测性

- [X] LLM 调用通过 Langfuse 追踪（压缩摘要、记忆总结、cronMem 事实抽取）（R-016）（注：Langfuse handler 在 agent_service.py 层注入）
- [X] Django logging 记录关键事件（R-016）：
  - [X] embedding 生成失败（WARNING）
  - [X] 压缩触发（INFO）
  - [X] 定时总结执行（INFO）
  - [X] 重试耗尽（WARNING）
  - [X] EmbeddingConfigNotFoundError（WARNING）
- [X] 日志级别：失败/异常 WARNING 及以上，正常流程 INFO

---

## 七、架构合规

- [X] 分层架构：View → Service → Repository
- [X] 禁止视图层业务逻辑
- [X] 禁止原生 SQL（使用 Django ORM + pgvector.django）（注：migration 中 RunSQL 用于扩展初始化，属例外）
- [X] 事务保护写操作
- [X] 异步任务使用 Celery（非 asyncio 原生）
- [X] LangGraph 流程工具集隔离（各流程不越界）
- [X] 记忆管理独立 app（apps/memory/）
- [X] 上下文管理作为 chat 服务层新增类

---

## 八、代码质量

- [X] Black 格式化通过
- [X] isort 排序通过
- [X] mypy 类型检查通过（所有公共函数有类型注解）
- [X] ESLint + Prettier 通过（前端）
- [X] 单元测试覆盖率 ≥ 80%（总体）
- [X] 服务层覆盖率 ≥ 95%
- [X] 数据模型层覆盖率 ≥ 90%
- [X] 仓库层覆盖率 ≥ 85%

---

## 九、数据库变更

- [X] PostgreSQL pgvector 扩展安装
- [X] PostgreSQL pg_jieba 扩展安装
- [X] user_memory 表创建（10 字段 + 6 索引）
- [X] user_memory_embedding 表创建（8 字段 + 2 索引）
- [X] content GIN 索引（tsvector + pg_jieba 中文分词）（注：在 migration RunSQL 中实现）
- [X] Django migration 文件生成并执行

---

## 十、基础设施变更

- [X] requirements.txt 新增依赖：tiktoken, pgvector, celery, django-celery-beat
- [X] core/celery.py 创建 + settings.py 配置
- [X] Redis DB2 用作 Celery Broker
- [X] Docker PostgreSQL 镜像升级（含 pgvector + pg_jieba）
- [X] docker-compose.yml 更新
- [X] Celery Worker + Beat 启动脚本
- [X] INSTALLED_APPS 新增 apps.memory

---

*文档版本：v3.0*
*创建日期：2026-01-30*
*更新日期：2026-01-31 — v3.0 基于五个模型文档全面重写，补充 PromptBuilder、pg_jieba、Celery、可观测性、基础设施变更等完整检查项*
*验证日期：2026-02-02 — 全部 89 项代码验证通过*
