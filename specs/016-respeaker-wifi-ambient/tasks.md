# Tasks: reSpeaker XVF3800 WiFi 无线环境语音接入

**Input**: Design documents from `/specs/016-respeaker-wifi-ambient/`
**Prerequisites**: plan.md (required), spec.md (required), research.md

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3, US4)
- US1 = 无线麦克风持续监听 (P1)
- US2 = LLM 意图分类主决策 (P1)
- US3 = 桥接服务健壮运行 (P2)
- US4 = TTS 输出到小爱音箱 (P3)

## Phase Mapping (plan.md → tasks.md)

| plan.md Phase | tasks.md Phase | 内容 |
|---------------|----------------|------|
| Phase 1 硬件准备 | Phase 2 Foundational | T004-T006 |
| Phase 2 桥接服务 | Phase 3 US1 | T008-T012 |
| Phase 3 LLM 意图 | Phase 4 US2 | T013-T017 |
| Phase 4 systemd | Phase 5 US3 | T018-T023 |
| Phase 5 测试验证 | 分散到各 Phase Checkpoint | — |
| — | Phase 1 Setup | T001-T003（plan 未单列） |
| — | Phase 6 US4 | T024-T028（plan 未单列，源码结构中标注 US4） |
| — | Phase 7 Polish | T029-T031 |

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: 创建桥接服务目录结构，注册设备获取 token

- [x] T001 创建桥接服务目录结构 `scripts/respeaker_bridge/`，包含 `__init__.py`
- [ ] T002 注册 reSpeaker 设备获取 API token：`POST /api/v1/voice/devices/` body=`{"name": "客厅reSpeaker"}`，保存 token 到 `scripts/respeaker_bridge/.env`
- [x] T003 [P] 验证 LinChat 虚拟环境已有 `websockets` 依赖（websockets 16.0 已安装）：`pip show websockets`，缺失则 `pip install websockets` 并更新 `backend/requirements.txt`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: 硬件固件刷写 + 后端配置变更，为所有 User Story 铺路

**⚠️ CRITICAL**: 硬件和配置就绪后才能开始 User Story 实现

- [ ] T004 刷入 XVF3800 I2S Slave 固件 v1.0.4（USB-C 连接靠近 3.5mm 口）：`sudo dfu-util -R -e -a 1 -D ~/github/reSpeaker_XVF3800_USB_4MIC_ARRAY/xmos_firmwares/i2s/respeaker_xvf3800_i2s_dfu_firmware_v1.0.4.bin`
- [ ] T005 烧录 ESP32-S3 Arduino 固件（`scripts/respeaker_bridge/firmware/respeaker_udp_stream.ino`）：Arduino IDE 选择 Board "XIAO_ESP32S3"，WiFi/UDP 配置已写入 `config.h`（SSID=`Dan&Huir_5G`，UDP→`192.168.3.119:12345`，宿主机 DNAT 已配置）。I2S 引脚：MCLK=GPIO9, BCLK=GPIO8, WS=GPIO7, DATA=GPIO44（PCB 原理图确认）
- [ ] T006 验证 UDP 数据可达 dev machine（经宿主机 192.168.3.119 DNAT 转发）：Python 脚本接收 UDP 包并打印帧大小（预期 1024 bytes/包），确认音频格式（16kHz/32-bit/2ch）。**决策门 A**：确认 32-bit PCM 是左对齐还是真 32-bit 值，结论决定 T008 转换方式（官方文档未明确，需实测）。**决策门 B 已关闭**：Channel 1（右声道）= ASR 自动选择波束已由官方文档确认（host_control/README.md Output Selection 章节），仅需实测验证音质
- [x] T007 [P] 修改 `backend/core/settings.py`：设置 `VOICE_DECISION_USE_LLM=True`，`VOICE_DECISION_LLM_TIMEOUT=5`，`VOICE_DECISION_LLM_THRESHOLD=0.6`

**Checkpoint**: 硬件 UDP 音频流可达，后端配置就绪

---

## Phase 3: User Story 1 - 无线麦克风持续监听 (Priority: P1) 🎯 MVP

**Goal**: reSpeaker WiFi 麦克风 → UDP → 桥接服务 → WebSocket → LinChat ambient → ASR 转录 → 聚合 → 决策

**Independent Test**: 设备说话 → 后端日志出现 `transcription.completed` + `decision.result`

### Implementation for User Story 1

- [x] T008 [US1] 实现音频格式转换模块 `scripts/respeaker_bridge/audio_converter.py`：32-bit/2ch → 16-bit/1ch（提取 Channel 1，struct 实现，转换方法依赖 T006 决策门结论：左对齐则右移 16 位，真 32-bit 则 clamp+截断），首包验证帧大小是否符合 16kHz/32-bit/2ch 预期，不匹配时记录 ERROR 日志。**帧验证策略**：仅首包验证格式（ESP32 固件格式启动后固定不变），后续帧按首包格式处理；若未来固件升级导致运行中格式变化，可扩展为逐帧验证。**注意**：设备到货前可用右移 16 位作为默认实现编写单元测试，T006 确认后调整
- [x] T008b [P] [US1] 编写 audio_converter 单元测试 `scripts/respeaker_bridge/tests/test_bridge.py`：构造 32-bit/2ch 测试数据，验证转换为 16-bit/1ch 的正确性
- [x] T009 [P] [US1] 实现配置管理模块 `scripts/respeaker_bridge/config.py`：从 `.env` 读取 UDP_PORT、WS_URL、DEVICE_TOKEN、LOG_LEVEL，提供默认值
- [x] T009b [P] [US1] 编写 config.py 单元测试 `scripts/respeaker_bridge/tests/test_bridge.py`（追加）：(1) 无 .env 文件时使用默认值；(2) .env 文件覆盖默认值；(3) 缺失必填项 DEVICE_TOKEN 时抛出明确错误
- [x] T010 [US1] 实现桥接服务主程序 `scripts/respeaker_bridge/bridge.py`：asyncio 事件循环，包含 UDP 服务器（接收音频帧）+ WebSocket 客户端（连接 LinChat，发送 `session.configure` 配置 ambient）+ 音频转发循环（UDP→converter→WS binary）+ 事件接收循环（接收并记录 LinChat JSON 事件）+ 优雅关闭处理（`loop.add_signal_handler` 捕获 SIGTERM/SIGINT → 关闭 WS 连接 → 停止 UDP 服务器 → 取消 asyncio 任务 → 退出事件循环）
- [x] T010b [P] [US1] 编写 bridge.py 核心转发逻辑单元测试 `scripts/respeaker_bridge/tests/test_bridge.py`（追加）：(1) mock UDP socket 接收音频帧 → 验证调用 audio_converter 并通过 WS 发送 binary；(2) mock WS 接收 JSON 事件 → 验证日志记录 transcription/decision/error；(3) WS 连接成功后验证发送 session.configure `{"mode":"ambient"}`；(4) UDP 帧到达时 WS 未连接 → 验证帧被丢弃不抛异常
- [x] T011 [US1] 添加桥接服务运行日志：启动信息、UDP 连接状态、WS 连接状态、每 60 秒输出帧统计（帧数/字节数/丢帧数/平均帧延迟ms）、错误日志
- [x] T011b [US1] 实现 ambient 设备独占检测：在 `backend/apps/voice/consumers.py` 的 VoiceConsumer 中，当新 ambient 连接建立时检查同 user_id 是否已有 device 类型的 ambient 连接。reSpeaker 设备在线时拒绝浏览器 ambient 连接（发送 error 事件 `{"type":"error","reason":"device_exclusive"}`）；浏览器先在线时 reSpeaker 连接踢掉浏览器连接；同类型 device 连接互踢（后到的 device 踢掉先到的 device，复用浏览器被踢逻辑）
- [x] T011c [P] [US1] 编写设备独占检测单元测试 `backend/tests/voice/test_device_exclusive.py`：(1) reSpeaker 设备已连接 ambient 时，浏览器新 ambient 连接收到 `{"type":"error","reason":"device_exclusive"}` 拒绝；(2) 浏览器已连接 ambient 时，reSpeaker 新连接成功并踢掉浏览器连接（浏览器断开前收到 `{"type":"error","reason":"device_exclusive","message":"reSpeaker 设备已连接"}`）；(3) 同类型设备（两个浏览器）不触发独占逻辑；(4) 连接类型通过 `self._is_device_connection` 判定（已有实现：device token 认证 → True，SysUser cookie → False，无需新增字段）；(5) 第一个 device 已连接 ambient 时，第二个 device 新连接成功并踢掉第一个 device 连接（被踢 device 断开前收到 `{"type":"error","reason":"device_exclusive","message":"新设备已连接"}`）
- [ ] T012 [US1] 手动 E2E 验证 + 延迟度量：启动桥接服务 → 对设备说话 → 检查后端日志 `transcription.completed` → 检查 `aggregation.completed` → 检查 `decision.result`。**延迟验证**：(a) SC-006: 对比桥接服务日志中 UDP 帧接收时间戳与 WS 发送时间戳，确认差值 ≤ 200ms；(b) SC-005: 对比 aggregation silence_timeout 触发时间与 Agent 首 token 时间，确认差值 ≤ 10s；(c) SC-001: 使用 spec.md 预定义的 10 句 ASR 测试集（见 Success Criteria 章节），逐句对设备朗读，按判定标准（关键实词覆盖+语义可理解）统计准确率 ≥ 85%。**边缘场景验证（P3，条件允许时执行）**：(d) 高噪音环境：在播放音乐/电视的房间对设备说话，观察 ASR 转录质量和决策结果；(e) WiFi 弱信号：将设备移至 WiFi 覆盖边缘，观察桥接服务帧统计中的丢帧率变化

**Checkpoint**: 核心音频链路跑通，设备说话能触发 LinChat ASR 转录和决策

---

## Phase 4: User Story 2 - LLM 意图分类主决策 (Priority: P1)

**Goal**: LLM 判断用户说话是否需要 AI 回复，替代唤醒词，超时默认 RECORD_ONLY

**Independent Test**: 说指令/问题 → RESPOND；说闲聊 → RECORD_ONLY；超时 → RECORD_ONLY

### Implementation for User Story 2

- [x] T013 [US2] 修改 `backend/apps/voice/services/response_decision_service.py`：LLM 超时后返回 `(DecisionResult.RECORD_ONLY, "llm_timeout")` 而非 `None`（当前 `None` 会穿透规则链）
- [x] T014 [US2] 增强 `backend/apps/context/templates/voice_intent_classify.j2` prompt：传入最近 5 条消息（时间倒排）+ 用户记忆摘要，明确三类判定（RESPOND/RECORD_ONLY），JSON 输出格式 `{"decision":"...","reason":"...","confidence":0.9}`
- [x] T015 [US2] 修改 `response_decision_service.py` 的 `_classify_intent_llm()` 方法：从 `apps.chat.repositories.message_repo` 获取最近 5 条消息（时间倒排，含 user/assistant 角色），从 `apps.memory.services.memory_service` 获取用户记忆摘要，拼入 prompt 上下文。消息格式复用 `apps.context.builder_helpers.pair_conversation_turns()` 保持与主 Agent 一致，记忆格式复用 `format_memory_block()`
- [x] T015b [P] [US2] 编写 `_classify_intent_llm()` 单元测试 `backend/tests/voice/test_response_decision_service.py`：(1) mock LLM 返回 RESPOND → 验证返回 DecisionResult.RESPOND；(2) mock LLM 返回 RECORD_ONLY → 验证返回 DecisionResult.RECORD_ONLY；(3) mock LLM 超时（asyncio.TimeoutError）→ 验证返回 (RECORD_ONLY, "llm_timeout") 而非 None；(4) 验证 prompt 中包含最近 5 条消息和用户记忆摘要；(5) 验证传入 prompt 的消息来自 message_repo 且格式经 pair_conversation_turns() 处理
- [ ] T016 [US2] 准确率测试：准备 20 条指令/问题测试集 + 20 条闲聊测试集，通过 ambient 模式逐条测试，统计 RESPOND/RECORD_ONLY 判定准确率（目标：指令 ≥90%，闲聊 ≥80%）
- [ ] T017 [US2] 根据 T016 测试结果调优 prompt 和 threshold，迭代至达到准确率目标

**Checkpoint**: LLM 意图分类作为主决策路径生效，无唤醒词

---

## Phase 5: User Story 3 - 桥接服务健壮运行 (Priority: P2)

**Goal**: 桥接服务 24 小时稳定运行，设备断电/后端重启自动恢复

**Independent Test**: 拔设备电源 → 重插 → 桥接服务自动恢复 → 后端重启 → WS 自动重连

### Implementation for User Story 3

- [x] T018 [US3] 在 `bridge.py` 添加 WebSocket 自动重连逻辑：检测断开 → 重连 5 次（线性递增间隔 3/6/9/12/15s）→ 重连后重发 `session.configure` 恢复 ambient 会话 → 5 次全部失败后记录 ERROR 日志，等待 60 秒重置计数器，重新开始重连循环（无限循环，不退出进程）
- [x] T018d [P] [US3] 编写 bridge.py 重连逻辑单元测试 `scripts/respeaker_bridge/tests/test_bridge.py`（追加）：(1) mock WebSocket 连接断开后触发重连，验证 5 次重试间隔为 3/6/9/12/15s；(2) 重连成功后验证重发 `session.configure` 消息；(3) 5 次全部失败后验证等待 60s 并重置计数器重新开始；(4) 重连期间 UDP 帧被丢弃不缓存
- [x] T019 [US3] 在 `bridge.py` 添加 UDP 流中断检测：30 秒无数据记录 WARNING，恢复时记录 INFO
- [x] T019b [P] [US3] 编写 UDP 流中断检测单元测试 `scripts/respeaker_bridge/tests/test_bridge.py`（追加）：(1) mock 30 秒无 UDP 数据 → 验证记录 WARNING 日志；(2) 中断后恢复 UDP 数据 → 验证记录 INFO 恢复日志；(3) 正常接收数据（间隔 < 30s）→ 不触发告警
- [x] T020 [US3] 在 `bridge.py` 添加启动时后端不可达处理：复用 FR-005 重连策略（5 次重试间隔 3/6/9/12/15s → 失败后等待 60s 重置 → 无限循环），不崩溃退出
- [x] T021 [US3] 创建 systemd 服务文件 `/etc/systemd/system/respeaker-bridge.service`：`WorkingDirectory=/home/dantsinghua/work/linchat/scripts/respeaker_bridge`、`Restart=always`、`RestartSec=5`、`PYTHONUNBUFFERED=1`、`After=network.target`（WorkingDirectory 确保 config.py 能找到 `.env` 文件）
- [ ] T022 [US3] 启用并测试 systemd 服务：`sudo systemctl enable respeaker-bridge` → 验证开机自启 → `sudo systemctl restart respeaker-bridge` → 验证崩溃重启
- [ ] T023 [US3] 稳定性验证：桥接服务运行 24 小时，期间手动断电设备 1 次、重启后端 1 次，确认自动恢复

**Checkpoint**: 桥接服务可 24 小时无人值守运行

---

## Phase 6: User Story 4 - TTS 输出到小爱音箱 (Priority: P3)

**Goal**: Agent 回复通过 HA media_player 播放到小爱音箱

**Independent Test**: 说"帮我开灯" → Agent 回复 → 小爱音箱播放 TTS

### Implementation for User Story 4

- [ ] T024 [US4] 确认小爱音箱 HA 集成能力（**决策门已关闭，结论如下**）：调研确认 TTS 调用方式为 `xiaomi_miot.intelligent_speaker` 服务（hass-xiaomi-miot 集成），参数 `text`=回复文本、`execute`=false、`silent`=false。REST API：`POST {HA_URL}/api/services/xiaomi_miot/intelligent_speaker`。**待 HA 启动后验证**：(1) 确认 hass-xiaomi-miot 集成已安装（`/api/services` 中有 `xiaomi_miot` 域）；(2) 通过 `ha_query(query_type="list", domain="media_player")` 获取小爱音箱实际 entity_id（格式 `media_player.xiaoai_{model}_{suffix}`）；(3) 实测一次 TTS 播报确认可用。**注意：字段是 `text` 不是 `message`，用错会静默失败**
- [ ] T024b [P] [US4] 配置 Nginx MinIO 音频代理（仅 TTS 降级路径需要）：在 `/etc/nginx/sites-available/deeptutor` 的 8080 端口 server 块中添加 `location /minio-audio/ { proxy_pass http://127.0.0.1:9010/linchat-audio/; }`，使 HA 容器（172.17.0.1）可通过 `http://192.168.3.x:8080/minio-audio/{key}` 访问 MinIO 音频文件。执行 `sudo nginx -t && sudo nginx -s reload` 验证。**注意**：仅当 T024 决策门确认需要 play_media 降级路径时才执行
- [x] T025 [US4] 在 `backend/apps/voice/services/tts_router.py` 新增 `send_to_ha_speaker()` 方法：优先通过 httpx 调用 `POST {HA_URL}/api/services/xiaomi_miot/intelligent_speaker`（Header `Authorization: Bearer {HA_TOKEN}`，body `{"entity_id": "{ha_speaker_entity_id}", "text": "{回复文本}", "execute": false, "silent": false}`），直传文本让音箱自带 TTS 合成播报，无需生成音频文件；若 `xiaomi_miot.intelligent_speaker` 服务不可用（HTTP 404/集成未安装），降级为：TTS 音频帧拼接为 WAV → 上传 MinIO → 通过 Nginx 代理生成局域网可达 URL → 调用 `POST {HA_URL}/api/services/media_player/play_media`（body `{"entity_id": "...", "media_content_id": url, "media_content_type": "music"}`）。`HA_URL` 和 `HA_TOKEN` 从 `settings.HA_URL` / `settings.HA_TOKEN` 读取（已有配置项）。**不引用 graph/tools/ 下的 Agent 工具函数**，保持服务层→外部 API 的清晰边界
- [x] T025b [P] [US4] 编写 `send_to_ha_speaker()` 单元测试 `backend/tests/voice/test_tts_router.py`（追加）：(1) mock `xiaomi_miot.intelligent_speaker` 服务可用（HTTP 200）→ 验证直传文本调用成功（请求 body 含 `text`/`execute`/`silent` 字段），不生成音频文件；(2) mock `intelligent_speaker` 返回 404（集成未安装）→ 验证降级为 WAV 拼接 + MinIO 上传 + `media_player.play_media` 调用；(3) mock HA 完全不可达（ConnectionError/Timeout）→ 验证抛出异常供 voice_pipeline 降级处理
- [x] T026 [US4] 在 `backend/apps/voice/models.py` 的 `VoiceSettings` 新增 `tts_output_device` 字段（CharField: "browser"/"ha_speaker"）和 `ha_speaker_entity_id` 字段（CharField, nullable）
- [x] T026b [US4] 生成并执行数据库迁移（迁移文件已生成：0004_add_tts_output_device.py，DB 离线待执行 migrate）：`python manage.py makemigrations voice` + `python manage.py migrate`
- [x] T026c [P] [US4] 更新 `backend/apps/voice/serializers.py`：在 `VoiceSettingsSerializer.Meta.fields` 中添加 `tts_output_device`、`ha_speaker_entity_id`；在 `VoiceSettingsUpdateSerializer` 中添加对应可选字段（`tts_output_device`: ChoiceField["browser","ha_speaker"]，`ha_speaker_entity_id`: CharField(allow_null=True)），含 validate 逻辑：当 `tts_output_device="ha_speaker"` 时 `ha_speaker_entity_id` 不可为空
- [x] T026d [P] [US4] 编写 serializer 验证单元测试 `backend/tests/voice/test_voice_settings_serializer.py`：(1) `tts_output_device="ha_speaker"` 且 `ha_speaker_entity_id` 为空/null → 验证返回 400 验证错误；(2) `tts_output_device="browser"` 且 `ha_speaker_entity_id` 为空 → 验证通过（browser 模式下 entity_id 可选）；(3) `tts_output_device="ha_speaker"` 且 `ha_speaker_entity_id="media_player.xiaomi_xxx"` → 验证通过正常保存；(4) `tts_output_device` 传入非法值（如 "usb"）→ 验证返回 400
- [x] T027 [US4] 修改 `backend/apps/voice/services/voice_pipeline.py`：根据 `VoiceSettings.tts_output_device` 选择 TTS 输出通道（browser → 现有 TTSRouter, ha_speaker → 新 `send_to_ha_speaker()`）。ha_speaker 播放失败（音箱不可达/超时/HTTP 5xx）时 MUST 降级到 browser 通道播放并记录 WARNING 日志，同时通过 Django Channels `group_send()` 向 `voice_{user_id}` group 的所有已连接客户端推送降级通知 `{"type":"warning","reason":"ha_speaker_unreachable","message":"音箱不可达，已降级到浏览器播放"}`（宪法 1.4 显式失败要求）。**实现机制**：VoiceConsumer 在 `connect()` 时已加入 `voice_{user_id}` group，voice_pipeline 通过 `get_channel_layer()` + `async_to_sync(group_send)()` 发送，无需持有 consumer 引用
- [x] T027b [P] [US4] 编写 TTS 路由单元测试 `backend/tests/voice/test_voice_pipeline_tts.py`：(1) `tts_output_device="browser"` → 走现有 TTSRouter；(2) `tts_output_device="ha_speaker"` → 调用 `send_to_ha_speaker()`；(3) ha_speaker 不可达（超时/异常）→ 降级到 browser 通道 + 验证 WARNING 日志 + 验证 WebSocket 发送 `{"type":"warning","reason":"ha_speaker_unreachable"}` 事件
- [ ] T028 [US4] E2E 验证：对设备说"帮我开灯" → Agent 执行 HA 工具 → TTS 回复播放到小爱音箱

**Checkpoint**: 完整闭环：麦克风采集→AI 决策→Agent 执行→音箱播报

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: 文档、日志、清理

- [x] T029 [P] 编写桥接服务 README：`scripts/respeaker_bridge/README.md`，包含安装步骤、配置说明、固件刷写指南、故障排查
- [ ] T030 [P] 更新 linchat-ops skill 添加桥接服务状态检查：`systemctl status respeaker-bridge`
- [ ] T030b [P] 运行覆盖率验证：(1) 桥接服务：`cd scripts/respeaker_bridge && pytest --cov=. --cov-report=term-missing tests/`，目标 ≥ 90%；(2) 后端服务层：`cd backend && pytest --cov=apps/voice/services --cov-report=term-missing tests/voice/`，目标 ≥ 95%（宪法 3.1）。覆盖率不达标则补充测试后重跑
- [ ] T031 提交所有变更并合并到 main 分支

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: 无依赖，立即开始
- **Foundational (Phase 2)**: T004-T006 依赖设备到货；T007 无依赖可提前执行
- **US1 (Phase 3)**: 依赖 Phase 1 + Phase 2 (T006 UDP 验证通过)
- **US2 (Phase 4)**: 仅依赖 T007（后端配置），**可与 US1 并行开发**
- **US3 (Phase 5)**: 依赖 US1 (Phase 3) 完成（桥接服务基础功能）
- **US4 (Phase 6)**: 依赖 US1 + US2 + HA media_player 可用
- **Polish (Phase 7)**: 依赖所有 User Story 完成

### User Story Dependencies

- **US1 (P1)**: 依赖硬件 + Phase 2 → 核心 MVP
- **US2 (P1)**: 仅依赖后端配置 → **可在设备到货前开发和测试**
- **US3 (P2)**: 依赖 US1 桥接服务存在
- **US4 (P3)**: 依赖 US1 + US2 + HA

### Parallel Opportunities

- T007（后端配置）可与 T004-T006（硬件）并行
- T008 和 T009（converter + config）可并行
- T013-T015（LLM 意图分类）可与 T008-T011（桥接服务）并行
- T029 和 T030（文档）可并行

---

## Parallel Example: 设备到货前可完成的工作

```bash
# 不依赖硬件，立即可做：
Task T001: "创建桥接服务目录结构"
Task T003: "验证 websockets 依赖"
Task T007: "修改 settings.py 开启 LLM 意图分类"
Task T008: "实现 audio_converter.py"（用测试数据验证）
Task T009: "实现 config.py"
Task T013: "修改 response_decision_service.py 超时行为"
Task T014: "增强 voice_intent_classify.j2 prompt"
Task T015: "修改 _classify_intent_llm() 加上下文"
```

---

## Implementation Strategy

### MVP First (US1 + US2)

1. **设备到货前**：完成 T001/T003/T007-T009/T013-T015（软件部分）
2. **设备到货后**：完成 T004-T006（硬件刷写）→ T010-T011（桥接服务主程序）→ T012（E2E）
3. **LLM 调优**：T016-T017（准确率测试和迭代）
4. **STOP and VALIDATE**: 核心链路全部跑通

### Incremental Delivery

1. Setup + Foundational → 基础就绪
2. US1 → 麦克风→LinChat 链路通 → MVP
3. US2 → 无唤醒词智能决策 → 核心体验完成
4. US3 → 24h 稳定运行 → 日常可用
5. US4 → 小爱音箱播放 → 完整闭环

---

## Notes

- T004-T006 依赖硬件到货，其余任务可提前开发
- audio_converter.py 可用构造的 32-bit/2ch 测试数据验证，不依赖真实设备
- LLM 意图分类（US2）可通过浏览器 ambient 模式测试，不依赖 reSpeaker
- US4（小爱音箱）需要先确认 HA 中 media_player 实体可用
