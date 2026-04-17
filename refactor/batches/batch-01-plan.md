# Batch-01: 修复声纹识别结果始终为 dantsinghua 的 bug

## 根因分析

### Layer 1: Feature flag 默认关闭（核心原因）
`VOICE_SPEAKER_IDENTIFICATION_ENABLED` 默认 `"false"`（settings.py:447），导致 017 声纹识别功能在运行时被完全跳过。所有音频走 `_legacy_aggregate()` → `speaker_user_id=0` → `target_uid=self.user_id` → 消息 `speaker_id=NULL`，前端显示 session owner 的用户名 "dantsinghua"。

### Layer 2: Gateway 识别质量低
即使开启，Gateway `/v1/voice/speakers/identify` 219/219 次返回 `identified=False`，置信度中位数 0.08（阈值 0.5）。数据库仅 1 条 SpeakerProfile（user_id=7, gw_id=anlin, name=dantsinghua, quality=0.85）。

### Layer 3: 异常路径丢失 speaker 标签
`_identify_ambient_speaker` 异常时返回 None → `_last_unknown_label` 被设为 None → 消息 `speaker_id=NULL`。

## 调查发现

| 检查项 | 结果 |
|--------|------|
| SpeakerProfile 数量 | 1（仅 dantsinghua） |
| 生产 .env feature flag | `VOICE_SPEAKER_IDENTIFICATION_ENABLED=true` |
| 运行时 flag 值（p0-fix worktree） | `False`（worktree 无 .env） |
| Gateway 连通性 | 不可达（frpc 隧道未启动） |
| VOICE_SPEAKER_THRESHOLD | 0.5 |

## 变更清单

| # | 文件 | 变更 |
|---|------|------|
| 1 | `backend/core/settings.py:447` | 默认值 `"false"` → `"true"` |
| 2 | `backend/apps/voice/services/speaker_service.py:98-104` | 增强诊断日志（conf/threshold/pcm_size），新增 `list_gateway_speakers()` 诊断方法 |
| 3 | `backend/apps/voice/consumer_events.py:90-94` | 异常路径调用 `_assign_unknown_label(None)` 而非设 None |
| 4 | `backend/tests/voice/test_speaker_identification.py` | 新增 4 个测试（exception fallback/not identified/disabled/multi-segment） |
| 5 | `backend/tests/voice/test_unknown_speaker_labeling.py` | 新增 4 个测试（null hash/reuse label/list_gateway_speakers success/error） |

## 验证

- 688 passed, 0 failed（全量 voice 测试套件，27.44s）
- 新增 8 个测试全部通过
- 无回归

## 待用户确认

1. Gateway 不可达，无法验证声纹识别端对端效果。Gateway 恢复后需手动测试。
2. 仅 1 个 SpeakerProfile，建议注册更多家庭成员声纹以提升识别效果。
3. `voice:unknown_counter` Redis 键无限增长问题建议延后处理（非 P0）。
