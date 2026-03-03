# tests/chat 测试指南

> **单用户单会话原则**: 一个用户永远对应一个会话，Message 中没有 conversation_id，只有 user_id。不存在多会话、不考虑并发会话。所有隔离按 user_id 粒度。

---

## 测试文件

| 文件 | 覆盖功能 |
|------|----------|
| `test_services.py` | ChatService / HistoryService / AgentService / LLM 异常映射 / 重试策略 |
| `test_views.py` | HTTP 视图 + SSE 流（chat/resume/reconnect）/ 请求验证 |
| `test_concurrency.py` | 并发消息处理、生成管理（register/unregister/signal_stop）、数据隔离 |
| `test_context_service.py` | ContextService 上下文构建/压缩/降级/安全截断 |
| `test_media_service.py` | MediaService 上传/验证/过期/RGBA 转换/维度检测 |
| `test_media_views.py` | 媒体上传/获取视图（含鉴权） |
| `test_media_attachment_repo.py` | MediaAttachmentRepository CRUD/UUID 查询/所有权验证/过期标记 |
| `test_media_cleanup_task.py` | Celery 媒体过期清理任务（批量/失败中止/CRITICAL 日志） |
| `test_minio_service.py` | MinioService 对象存储 CRUD/预签名 URL/上传字节流 |
| `test_inference_service.py` | InferenceService Redis SETNX/TTL/任务序列化反序列化 |
| `test_inference_cancel.py` | 推理取消流程（Pub/Sub 信号/轮询回退/request_id 校验） |
| `test_model_routing.py` | 多模态模型路由（文本→默认/图片/视频/音频→minicpm-o） |
| `test_video_processing.py` | 视频预处理（ffmpeg/ffprobe mock）+ video_url 消息构建 |
| `test_audio_processing.py` | 音频多模态消息处理（WebM/WAV/MP3/MIME/时长检测） |
| `test_document_parse_service.py` | DocumentParseService 创建/轮询/结果获取（markdown/json） |
| `test_document_parse_views.py` | 文档解析视图（所有权验证/错误码映射/Gateway 错误） |
| `test_prompts.py` | PromptBuilder/PromptModule/TrimLevel/模板占位符/PROTECTED 级别 |
| `test_tools.py` | Agent 上下文工具（compact/extract/prune） |
| `test_agent.py` | Agent 工厂（chat/context/memory/cronMem）/ get_llm / _wrap_prompt |

---

## 运行命令

```bash
cd /home/dantsinghua/work/linchat/backend && source ../linchat/bin/activate && pytest tests/chat/ -v
```

## 注意事项

1. `test_media_cleanup_task.py` 需要真实 PostgreSQL（`--reuse-db`）
2. 视频/音频测试 mock 了 ffmpeg/ffprobe 子进程，无需实际安装
3. `test_media_views.py` 部分测试因鉴权环境标记 skip
4. 异步测试使用 `tests.helpers.run_async()` 辅助函数
5. `test_concurrency.py` 使用 `asyncio.gather` 模拟 10 用户并发压力测试


<claude-mem-context>
# Recent Activity

### Feb 13, 2026

| ID | Time | T | Title | Read |
|----|------|---|-------|------|
| #1048 | 11:01 AM | 🔵 | Model Routing Tests Expect Wrong Default Values Confirming Agent3 Bug | ~597 |
</claude-mem-context>