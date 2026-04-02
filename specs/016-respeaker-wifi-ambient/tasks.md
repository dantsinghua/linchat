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

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: 创建桥接服务目录结构，注册设备获取 token

- [ ] T001 创建桥接服务目录结构 `scripts/respeaker_bridge/`，包含 `__init__.py`
- [ ] T002 注册 reSpeaker 设备获取 API token：`POST /api/v1/voice/devices/` body=`{"name": "客厅reSpeaker"}`，保存 token 到 `scripts/respeaker_bridge/.env`
- [ ] T003 [P] 验证 LinChat 虚拟环境已有 `websockets` 依赖：`pip show websockets`，缺失则 `pip install websockets` 并更新 `backend/requirements.txt`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: 硬件固件刷写 + 后端配置变更，为所有 User Story 铺路

**⚠️ CRITICAL**: 硬件和配置就绪后才能开始 User Story 实现

- [ ] T004 刷入 XVF3800 I2S 固件（USB-DFU）：`sudo dfu-util -R -e -a 1 -D respeaker_xvf3800_usb_dfu_firmware_v2.0.7.bin`
- [ ] T005 刷入 ESP32-S3 UDP 音频流固件（Arduino IDE，Seeed 官方示例），配置 WiFi SSID/密码和目标 UDP 地址（dev machine IP:端口）
- [ ] T006 验证 UDP 数据可达 dev machine：Python 脚本接收 UDP 包并打印帧大小/采样率，确认音频格式（16kHz/32-bit/2ch）
- [ ] T007 [P] 修改 `backend/core/settings.py`：设置 `VOICE_DECISION_USE_LLM=True`，`VOICE_DECISION_LLM_TIMEOUT=5`，`VOICE_DECISION_LLM_THRESHOLD=0.6`

**Checkpoint**: 硬件 UDP 音频流可达，后端配置就绪

---

## Phase 3: User Story 1 - 无线麦克风持续监听 (Priority: P1) 🎯 MVP

**Goal**: reSpeaker WiFi 麦克风 → UDP → 桥接服务 → WebSocket → LinChat ambient → ASR 转录 → 聚合 → 决策

**Independent Test**: 设备说话 → 后端日志出现 `transcription.completed` + `decision.result`

### Implementation for User Story 1

- [ ] T008 [P] [US1] 实现音频格式转换模块 `scripts/respeaker_bridge/audio_converter.py`：32-bit/2ch → 16-bit/1ch（提取 Channel 1，struct 实现），首包验证帧大小是否符合 16kHz/32-bit/2ch 预期，不匹配时记录 ERROR 日志
- [ ] T008b [P] [US1] 编写 audio_converter 单元测试 `tests/test_respeaker_bridge.py`：构造 32-bit/2ch 测试数据，验证转换为 16-bit/1ch 的正确性
- [ ] T009 [P] [US1] 实现配置管理模块 `scripts/respeaker_bridge/config.py`：从 `.env` 读取 UDP_PORT、WS_URL、DEVICE_TOKEN、LOG_LEVEL，提供默认值
- [ ] T010 [US1] 实现桥接服务主程序 `scripts/respeaker_bridge/bridge.py`：asyncio 事件循环，包含 UDP 服务器（接收音频帧）+ WebSocket 客户端（连接 LinChat，发送 `session.configure` 配置 ambient）+ 音频转发循环（UDP→converter→WS binary）+ 事件接收循环（接收并记录 LinChat JSON 事件）
- [ ] T011 [US1] 添加桥接服务运行日志：启动信息、UDP 连接状态、WS 连接状态、每 60 秒输出帧统计（帧数/字节数/丢帧数/平均帧延迟ms）、错误日志
- [ ] T012 [US1] 手动 E2E 验证 + 延迟度量：启动桥接服务 → 对设备说话 → 检查后端日志 `transcription.completed` → 检查 `aggregation.completed` → 检查 `decision.result`。**延迟验证**：(a) SC-006: 对比桥接服务日志中 UDP 帧接收时间戳与 WS 发送时间戳，确认差值 ≤ 200ms；(b) SC-005: 对比 aggregation silence_timeout 触发时间与 Agent 首 token 时间，确认差值 ≤ 10s；(c) SC-001: 准备 10 句标准中文短句，逐句对设备朗读，人工对比转录结果，统计准确率 ≥ 85%

**Checkpoint**: 核心音频链路跑通，设备说话能触发 LinChat ASR 转录和决策

---

## Phase 4: User Story 2 - LLM 意图分类主决策 (Priority: P1)

**Goal**: LLM 判断用户说话是否需要 AI 回复，替代唤醒词，超时默认 RECORD_ONLY

**Independent Test**: 说指令/问题 → RESPOND；说闲聊 → RECORD_ONLY；超时 → RECORD_ONLY

### Implementation for User Story 2

- [ ] T013 [US2] 修改 `backend/apps/voice/services/response_decision_service.py`：LLM 超时后返回 `(DecisionResult.RECORD_ONLY, "llm_timeout")` 而非 `None`（当前 `None` 会穿透规则链）
- [ ] T014 [US2] 增强 `backend/apps/context/templates/voice_intent_classify.j2` prompt：传入最近 5 条消息（时间倒排）+ 用户记忆摘要，明确三类判定（RESPOND/RECORD_ONLY），JSON 输出格式 `{"decision":"...","reason":"...","confidence":0.9}`
- [ ] T015 [US2] 修改 `response_decision_service.py` 的 `_classify_intent_llm()` 方法：通过 `apps.chat.repositories.message_repo` 查询最近 5 条消息 + `apps.memory.services.memory_service` 搜索用户记忆，拼入 prompt 上下文
- [ ] T015b [P] [US2] 编写 `_classify_intent_llm()` 单元测试 `backend/tests/apps/voice/test_response_decision_service.py`：(1) mock LLM 返回 RESPOND → 验证返回 DecisionResult.RESPOND；(2) mock LLM 返回 RECORD_ONLY → 验证返回 DecisionResult.RECORD_ONLY；(3) mock LLM 超时（asyncio.TimeoutError）→ 验证返回 (RECORD_ONLY, "llm_timeout") 而非 None；(4) 验证 prompt 中包含最近 5 条消息和用户记忆摘要
- [ ] T016 [US2] 准确率测试：准备 20 条指令/问题测试集 + 20 条闲聊测试集，通过 ambient 模式逐条测试，统计 RESPOND/RECORD_ONLY 判定准确率（目标：指令 ≥90%，闲聊 ≥80%）
- [ ] T017 [US2] 根据 T016 测试结果调优 prompt 和 threshold，迭代至达到准确率目标

**Checkpoint**: LLM 意图分类作为主决策路径生效，无唤醒词

---

## Phase 5: User Story 3 - 桥接服务健壮运行 (Priority: P2)

**Goal**: 桥接服务 24 小时稳定运行，设备断电/后端重启自动恢复

**Independent Test**: 拔设备电源 → 重插 → 桥接服务自动恢复 → 后端重启 → WS 自动重连

### Implementation for User Story 3

- [ ] T018 [US3] 在 `bridge.py` 添加 WebSocket 自动重连逻辑：检测断开 → 重连 5 次（间隔 3/6/9/12/15s）→ 重连后重发 `session.configure` → 超过重试次数记录 ERROR 并继续等待
- [ ] T019 [US3] 在 `bridge.py` 添加 UDP 流中断检测：30 秒无数据记录 WARNING，恢复时记录 INFO
- [ ] T020 [US3] 在 `bridge.py` 添加启动时后端不可达处理：持续重试 WS 连接，不崩溃退出
- [ ] T021 [US3] 创建 systemd 服务文件 `/etc/systemd/system/respeaker-bridge.service`：`Restart=always`、`RestartSec=5`、`PYTHONUNBUFFERED=1`、`After=network.target`
- [ ] T022 [US3] 启用并测试 systemd 服务：`sudo systemctl enable respeaker-bridge` → 验证开机自启 → `sudo systemctl restart respeaker-bridge` → 验证崩溃重启
- [ ] T018b [US3] 实现 ambient 设备独占检测：在 `backend/apps/voice/consumers.py` 的 VoiceConsumer 中，当新 ambient 连接建立时检查同 user_id 是否已有 device 类型的 ambient 连接。reSpeaker 设备在线时拒绝浏览器 ambient 连接（发送 error 事件 `{"type":"error","reason":"device_exclusive"}`）；浏览器先在线时 reSpeaker 连接踢掉浏览器连接
- [ ] T023 [US3] 稳定性验证：桥接服务运行 24 小时，期间手动断电设备 1 次、重启后端 1 次，确认自动恢复

**Checkpoint**: 桥接服务可 24 小时无人值守运行

---

## Phase 6: User Story 4 - TTS 输出到小爱音箱 (Priority: P3)

**Goal**: Agent 回复通过 HA media_player 播放到小爱音箱

**Independent Test**: 说"帮我开灯" → Agent 回复 → 小爱音箱播放 TTS

### Implementation for User Story 4

- [ ] T024 [US4] 确认小爱音箱在 HA 中注册为 `media_player` 实体：通过 `ha_query(query_type="list", domain="media_player")` 查找 entity_id
- [ ] T025 [US4] 在 `backend/apps/voice/services/tts_router.py` 新增 `send_to_ha_speaker()` 方法：将 TTS 音频帧拼接为 WAV → 上传 MinIO → 生成 presigned URL → 调用 `ha_control(entity_id, "play", {"media_url": url})`
- [ ] T026 [US4] 在 `backend/apps/voice/models.py` 的 `VoiceSettings` 新增 `tts_output_device` 字段（CharField: "browser"/"ha_speaker"）和 `ha_speaker_entity_id` 字段（CharField, nullable）
- [ ] T026b [US4] 生成并执行数据库迁移：`python manage.py makemigrations voice` + `python manage.py migrate`
- [ ] T027 [US4] 修改 `backend/apps/voice/services/voice_pipeline.py`：根据 `VoiceSettings.tts_output_device` 选择 TTS 输出通道（browser → 现有 TTSRouter, ha_speaker → 新 `send_to_ha_speaker()`）
- [ ] T028 [US4] E2E 验证：对设备说"帮我开灯" → Agent 执行 HA 工具 → TTS 回复播放到小爱音箱

**Checkpoint**: 完整闭环：麦克风采集→AI 决策→Agent 执行→音箱播报

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: 文档、日志、清理

- [ ] T029 [P] 编写桥接服务 README：`scripts/respeaker_bridge/README.md`，包含安装步骤、配置说明、固件刷写指南、故障排查
- [ ] T030 [P] 更新 linchat-ops skill 添加桥接服务状态检查：`systemctl status respeaker-bridge`
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
