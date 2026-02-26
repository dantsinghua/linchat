# tests/chat 测试指南

> chat 模块测试集，覆盖服务层、视图层、数据层、Agent、Prompt、多模态和推理管理功能。

---

## 测试文件

| 文件 | 测试目标 | 覆盖模块 |
|------|---------|---------|
| `test_services.py` | ChatService / HistoryService / AgentService / LLM 异常映射 | `chat.services` |
| `test_views.py` | HTTP 视图 + SSE 流（chat/resume/reconnect） | `chat.views` |
| `test_concurrency.py` | 并发消息处理、生成管理、数据隔离 | `chat.services.chat_service` |
| `test_media_service.py` | MediaService（上传/验证/过期/维度检测） | `chat.services.media_service` |
| `test_media_views.py` | 媒体上传/获取视图（含鉴权） | `chat.views` |
| `test_media_attachment_repo.py` | MediaAttachmentRepository（CRUD/UUID查询/过期标记） | `chat.repositories` |
| `test_media_cleanup_task.py` | Celery 媒体过期清理任务（批量/失败中止） | `chat.tasks` |
| `test_minio_service.py` | MinioService（对象存储 CRUD/预签名 URL） | `chat.services.minio_service` |
| `test_inference_service.py` | InferenceService（Redis 推理任务 SETNX/TTL） | `chat.services.inference_service` |
| `test_inference_cancel.py` | 推理取消流程（取消/Pub-Sub 信号/轮询回退） | `chat.services.inference_service` |
| `test_model_routing.py` | 多模态模型路由（文本/图片/视频/音频→minicpm-o） | `graph.agent` |
| `test_video_processing.py` | 视频预处理 + 多模态消息构建（ffmpeg/ffprobe） | `graph.agent` |
| `test_audio_processing.py` | 音频多模态消息处理（格式/时长/模型选择） | `graph.agent` |
| `test_document_parse_service.py` | DocumentParseService（创建/轮询/结果获取） | `chat.services.document_parse_service` |
| `test_document_parse_views.py` | 文档解析视图（所有权验证/错误码映射） | `chat.views` |
| `test_context_service.py` | ContextService（上下文构建/压缩/降级/安全截断） | `chat.services.context_service` |
| `test_prompts.py` | PromptBuilder / PromptModule / TrimLevel / 模板 | `graph.prompts` |
| `test_tools.py` | Agent 上下文工具（compact/extract/prune） | `graph.tools` |
| `test_agent.py` | Agent 工厂（chat/context/memory/cronMem）/ _wrap_prompt | `graph.agent` |

---

## 运行命令

```bash
source /home/dantsinghua/work/linchat/linchat/bin/activate
cd /home/dantsinghua/work/linchat/backend

# 全部 chat 测试
pytest tests/chat/ -v

# 单个文件
pytest tests/chat/test_media_service.py -v

# 带覆盖率
pytest tests/chat/ --cov=apps/chat --cov-report=term-missing
```

---

## 重要 Fixture 和 Mock

### 共享辅助函数（`tests/helpers.py`）

| 函数 | 用途 |
|------|------|
| `run_async(coro)` | 在同步测试中运行异步协程 |
| `collect_stream(async_gen)` | 收集异步生成器全部结果 |

### 核心 Mock 策略

所有外部依赖必须 mock，禁止真实调用：

| Mock 目标 | 说明 |
|-----------|------|
| `message_repo` / `media_attachment_repo` / `execution_repo` | 数据库操作 |
| `minio_service` | MinIO 对象存储 |
| `AgentService.execute` | Agent 执行（返回 AsyncMock 生成器） |
| `httpx.AsyncClient` | 外部 HTTP（Gateway / DocumentParse） |
| `inference_service` | Redis 推理任务管理 |
| `EventService.publish_event` | SSE 事件推送 |
| `apps.graph.agent.model_service` | 模型配置读取 |
| `apps.graph.agent.ChatOpenAI` | LLM 实例创建 |
| `apps.graph.agent.create_react_agent` | LangGraph Agent 创建 |
| `subprocess.run` / `asyncio.create_subprocess_exec` | ffprobe/ffmpeg 子进程 |
| `apps.common.tokenizer.count_tokens` | Token 计数 |

### 特殊 Fixture

- **`test_media_cleanup_task.py`**: 使用 `django.test.TestCase`（需要真实数据库），不是纯 mock 测试
- **`test_concurrency.py`**: 使用 `asyncio.gather` 并发执行，模拟 10 用户压力测试（SC-004）
- **`test_context_service.py`**: mock `get_redis_client` 模拟 Redis 不可用降级场景

---

## 测试覆盖的功能点

### 聊天服务（test_services.py）
- LLM 异常映射：ConnectionError/Timeout/RateLimit/ContentFilter/QuotaExceeded → 对应错误码
- 生成管理：register/unregister/signal_stop
- ChatService：空消息/超长/截断/停止/恢复/重连
- HistoryService：首页加载/游标分页/生成中消息查找
- AgentService.execute：无 token/成功/中断/异常/超时/token 统计
- LLM 重试策略验证

### 视图层（test_views.py）
- SSE 视图：METHOD NOT ALLOWED / 无效 JSON / 空内容 / 超长内容
- resume_generation：缺少 request_id
- reconnect_stream：METHOD NOT ALLOWED

### 媒体处理
- **MediaService**: 文件类型/大小验证、RGBA 转换、维度检测、过期检查
- **MinioService**: 上传文件/字节流、下载、删除、存在检查、预签名 URL
- **Repository**: UUID 查询（含所有权验证）、批量查询、过期标记
- **清理任务**: 过期记录清理、MinIO 删除失败跳过、连续 10 次失败中止（CRITICAL 日志）

### 推理管理
- **InferenceService**: SETNX 原子注册、TTL 刷新、任务序列化/反序列化
- **取消流程**: 正常取消/无活跃任务/request_id 不匹配、Pub/Sub 信号监听、轮询回退

### 多模态处理
- **模型路由**: 纯文本→默认模型、图片/视频/音频→minicpm-o、混合媒体优先级
- **视频**: 格式/大小验证、ffprobe 时长检测、ffmpeg 预处理、video_url 消息构建
- **音频**: WebM/WAV/MP3 格式、MIME 类型区分、ffprobe 时长检测、语音占位替换

### 文档解析
- **Service**: 创建解析任务、轮询状态、获取结果（markdown/json）
- **Views**: 所有权验证、错误码映射（T075）、Gateway 错误处理

### Prompt 系统（test_prompts.py）
- PromptConfig 默认值和 effective_window 计算
- PromptBuilder 模块管理（enable/disable/chaining）
- 组件构建：system prompt、memory block（排序/截断）、compaction、tool context、conversation history
- TrimLevel 优先级：L1(对话) → L2(工具) → L3(记忆)
- PROTECTED 级别消息不可裁剪
- 模板占位符完整性验证

### Agent 工厂（test_agent.py）
- get_llm：Qwen3 传递 enable_thinking=False、非 Qwen3 不传
- 四流程工具集隔离：chat（SubAgent 工具）、context（3 工具）、memory（4 工具）、cronMem（0 工具）
- _wrap_prompt：超长历史裁剪、短历史保留、最低 2000 token 预算兜底

---

## 注意事项

1. **数据库依赖**: `test_media_cleanup_task.py` 需要真实 PostgreSQL 数据库（`--reuse-db`）
2. **ffmpeg/ffprobe**: 视频和音频测试 mock 了子进程调用，不需要实际安装
3. **BRAVE_SEARCH_API_KEY**: Agent 测试中 `search_subagent` 注册受此环境变量控制
4. **异步测试**: 大量使用 `run_async()` 辅助函数，部分使用 `@pytest.mark.asyncio`
5. **鉴权环境**: `test_media_views.py` 多数测试因需要完整鉴权环境而标记 skip
