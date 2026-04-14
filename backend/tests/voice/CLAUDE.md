# tests/voice 测试指南

> **单用户单会话原则**: 一个用户永远对应一个会话，Message 中没有 conversation_id，只有 user_id。不存在多会话、不考虑并发会话。所有隔离按 user_id 粒度。

---

## 测试文件

| 文件 | 覆盖模块 | 测试函数数 |
|------|----------|-----------|
| `test_asr_stream_client.py` | ASRStreamClient(BaseWSClient) — 连接/配置/PCM 帧转发/事件回调/断开 | 9 |
| `test_consumers.py` | VoiceConsumer — Cookie/Token 认证、session.configure、Binary 帧透传、ASR 事件翻译、session.close/disconnect 清理、response.cancel、SESSION_CONFLICT、频率限制、ASR 断开处理 | 32 |
| `test_coverage_boost.py` | 补充覆盖率 — voice_messages.build_agent_error 分支、ws_client_base cleanup/receive_loop 分支、tts_pipeline_manager cancel/shutdown/play/drain 分支、voice_session_service.add_recent_speaker、tts_router.send_warning + HA 音箱非预期响应 | 35 |
| `test_device_exclusive.py` | SessionMixin._check_device_exclusive — 设备阻止浏览器/设备踢浏览器/浏览器间无独占/设备间踢换/断开注销/TTL | 11 |
| `test_device_service.py` | DeviceService — 设备注册/撤销/Token 认证/SM4 加密（UUID + token_prefix） | 40 |
| `test_latency_benchmark.py` | 端到端语音延迟基准 — 音频→ASR→Pipeline 启动、网络延迟模拟、多轮对话延迟 | 6 |
| `test_models.py` | SpeakerProfile / RegisteredDevice / VoiceSettings / Message voice 字段 | 60 |
| `test_repositories.py` | SpeakerProfile/RegisteredDevice/VoiceSettings Repo CRUD | 45 |
| `test_response_decision.py` | ResponseDecisionService — 紧急停止/唤醒词精确+模糊/活跃对话/多 speaker/问句特征/默认决策/唤醒词加载/超时/Redis 错误/编辑距离/拼音相似度 | 86 |
| `test_response_decision_llm.py` | LLM 意图分类增强 — 高/低置信度/超时/禁用/连接异常/非 ambient 跳过/无活跃模型/非法 JSON/HTTP 错误/优先级交互/classify_intent_llm 直接调用/上下文获取/Prompt 内容验证 | 39 |
| `test_speaker_service.py` | SpeakerService — 声纹注册/删除/识别/列表/Gateway 删除/注册异常/Gateway 配置 | 28 |
| `test_tts_ha_speaker.py` | TTSRouter.send_to_ha_speaker — xiaomi_miot.intelligent_speaker 成功/404 降级 play_media/HA 不可达/超时/500 错误 | 8 |
| `test_tts_pipeline_manager.py` | TTSPipelineManager — 安慰递进/错误播报/cancel/shutdown/段间 gap | 13 |
| `test_tts_router.py` | TTSRouter — group_name 格式/send_binary/send_control/on_audio callback/init | 21 |
| `test_tts_stream_client.py` | TTSStreamClient(BaseWSClient) — 连接/configure/text.delta/receive_loop binary→on_audio/audio.done/error 日志/ConnectionClosed/wait_for_done 超时 | 11 |
| `test_utterance_aggregator.py` | UtteranceAggregator — 单/多段聚合/timer 重置/max_buffer 自动 flush/flush/reset/空缓冲区/空字符串忽略/状态流转/destroy/属性/回调异常安全/默认设置 | 29 |
| `test_views.py` | REST API 视图 — 声纹/设备/设置 CRUD + 认证 + 用户隔离 + 方法限制 | 60 |
| `test_voice_pipeline.py` | VoicePipeline — 正常流程/Agent 错误/TTS 集成/TTS 降级/TTS 禁用/response 事件序列/StreamChunk 全类型/管道互斥 barge-in/取消/TTS 超时/Guard/音频持久化/ambient RESPOND/ambient RECORD_ONLY/RECORD_ONLY 清理 | 32 |
| `test_voice_pipeline_tts.py` | VoicePipeline._try_ha_speaker_tts — browser 模式跳过/ha_speaker 模式调用/HASpeakerError 降级 | 8 |
| `test_voice_session.py` | VoiceSessionService + VoicePersistService — Redis 会话 CRUD/单会话强制/TTL/活跃对话标记/音频帧缓存/PCM→WAV/时长计算/MinIO 上传删除/LLM 频率限制/Redis key 格式 | 38 |
| `test_voice_settings_serializer.py` | VoiceSettingsUpdateSerializer — ha_speaker 必须指定 entity_id/browser 模式无需/有效 entity_id/无效 tts_output_device/可选字段 | 11 |
| `test_voice_settings_service.py` | VoiceSettingsService — get_settings 已有/自动创建/update_settings 完整流程 | 8 |

**合计**: 22 个测试文件，680+ 个测试函数

---

## 运行命令

```bash
cd /home/dantsinghua/work/linchat/backend && source ../linchat/bin/activate && pytest tests/voice/ -v
# 按子模块运行
pytest tests/voice/test_consumers.py -v          # Consumer 测试
pytest tests/voice/test_response_decision.py -v  # 响应决策测试
pytest tests/voice/test_voice_pipeline.py -v     # 语音管道测试
pytest tests/voice/test_latency_benchmark.py -v  # 延迟基准（标记 @pytest.mark.benchmark）
```

## 核心 Mock

| Mock 目标 | 用途 |
|-----------|------|
| `websockets.connect` | Gateway ASR/TTS WebSocket 连接（BaseWSClient 底层） |
| `apps.voice.services.speaker_service.httpx.AsyncClient` | 声纹 Gateway HTTP 请求 |
| `apps.graph.services.agent_service.AgentService` | Agent 推理 |
| `apps.graph.services.inference_service.InferenceService` | 推理任务管理 |
| `apps.voice.services.voice_persist_service` | PCM→WAV + MinIO 上传/删除 |
| `apps.voice.services.voice_session_service` | Redis 会话状态/频率限制 |
| `apps.voice.services.response_decision_service` | 唤醒词/响应决策 |
| `redis.asyncio.Redis` | 会话状态 / 音频缓存 / 频率限制 |
| `core.redis` (redis_get/redis_setex_json/redis_delete) | 设备独占 ambient 连接注册 |
| `channels.testing.WebsocketCommunicator` | Consumer 端到端测试 |
| `channels.layers.get_channel_layer` | TTSRouter Channels 分组广播 |
| `apps.users.services.sm4_encrypt/sm4_decrypt` | SM4 设备 Token 加解密 |
| `httpx.AsyncClient` | LLM 意图分类 HTTP 请求 / HA 音箱控制 |
| `apps.models.services.model_service.get_active_model` | 获取活跃工具模型配置 |
| `apps.voice.repositories.voice_settings_repo` | 唤醒词配置/语音设置 |
| `apps.voice.services.tts_router.TTSRouter` | HA 音箱 TTS 路由 |
| `apps.common.storage.minio_service.MinIOService` | MinIO 对象存储 |

## 注意事项

1. ASR/TTS 测试通过 mock `websockets.connect` 实现，无需真实 Gateway
2. `test_repositories.py` 需要真实 PostgreSQL（`--reuse-db`）
3. `test_voice_pipeline.py` mock 了 AgentService + TTSPipelineManager + 持久化服务
4. `test_tts_pipeline_manager.py` mock 了 TTSStreamClient + settings（极短 delay 加速测试）
5. `test_response_decision.py` mock 了 Redis（活跃对话/说话人集合）和 pypinyin
6. `test_utterance_aggregator.py` 使用极短超时（0.05-0.1s）加速而非 mock 时间，基于真实 asyncio 时序
7. `test_response_decision_llm.py` mock httpx.AsyncClient + get_active_model + settings，13 个测试类覆盖高/低置信度、超时、非 ambient 跳过、优先级交互、classify_intent_llm 直接调用、上下文获取、Prompt 内容
8. `test_tts_router.py` mock channels.layers.get_channel_layer，5 个测试类覆盖 group_name/binary/control/callback/init
9. `test_device_exclusive.py` mock core.redis + channel_layer.send，7 个测试类覆盖设备独占/踢换/注销
10. `test_tts_ha_speaker.py` mock httpx.AsyncClient + MinIOService，5 个测试类覆盖 intelligent_speaker 成功/降级/异常
11. `test_voice_settings_serializer.py` 无 mock，直接验证 DRF Serializer 校验逻辑
12. `test_voice_settings_service.py` mock voice_settings_repo，2 个测试类覆盖 get/update
13. `test_coverage_boost.py` 跨多个服务补充覆盖率缺失行（voice_messages/ws_client_base/tts_pipeline_manager/voice_session_service/tts_router）
14. `test_voice_pipeline_tts.py` mock TTSRouter.send_to_ha_speaker，3 个测试类覆盖 browser 跳过/ha_speaker 调用/降级
15. 异步测试使用 `pytest-asyncio`（`@pytest.mark.asyncio`）
