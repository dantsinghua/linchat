# Batch batch-33 执行计划

> 生成时间：2026-07-17 | 基线 HEAD：42bf780
> 类型：refactor | 优先级：P2 | 风险：high
> 预估：5 文件 / 120 行 / 1 session
> 依赖：无（depends_on=[]，无需前置校验）
> SLO 影响：无（blocks_slo=null）

## 1. 任务理解（一句话）

把 voice service 层 8 处绕过 repositories 直连 `Message.objects` / `MediaAttachment.objects`
的 ORM 调用，统一收敛到 `chat.repositories.message_repo`（及 media 侧 repo），
与已合规的 `ambient_light_service.py` 一致，纯分层收敛、零运行时行为变化、不碰 schema、隔离粒度仍为 user_id。

## 2. 涉及文件清单与改动预测

| # | 文件 | 当前行数 | 预计改动行数 | 改动类型 | 风险 | 精简潜力 |
|---|------|---------|------------|---------|------|---------|
| 1 | backend/apps/voice/services/voice_persist_service.py | 143 | +6 -18 | 调用点改走 repo | 高 | 中（净减 ~12 行） |
| 2 | backend/apps/voice/services/voice_pipeline.py | 329 | +2 -6 | 调用点改走 repo | 高 | 低（仍 >300 见 §7） |
| 3 | backend/apps/voice/services/speaker_service.py | 191 | +2 -6 | 调用点改走 repo | 中 | 低 |
| 4 | backend/apps/chat/repositories.py | 107 | +40 | repo 补方法 | 中 | 低 |
| 5 | backend/apps/media/repositories.py（**新增到 scope**） | 201 | +12 | repo 补方法 | 中 | 低 |
| 6 | backend/tests/voice/test_voice_persist_service.py | 230 | +30 | 回归/新增测试 | 低 | 低 |

> 注：scope 原列 5 文件，但 MediaAttachment 属 media app，其 create 必须落在 `media_attachment_repo`（见 §7 待确认 1），实际需 6 文件。

## 3. 直连点清单（rg 核实，8 处，与 diag 一致）

| # | 位置 | 当前调用 | 语义 | 所在函数（同步性） |
|---|------|---------|------|------------------|
| 1 | voice_persist_service.py:82 | `Message.objects.filter(request_id,user_id,role="user").first()` | 取用户消息 | `_atomic_mark_voice`（**sync，transaction.atomic 内**） |
| 2 | voice_persist_service.py:86 | `MediaAttachment.objects.create(...)` | 建音频附件 | 同上（sync/atomic） |
| 3 | voice_persist_service.py:92 | `Message.objects.filter(request_id,user_id,role="assistant").first()` | 取助手消息 | 同上（sync/atomic） |
| 4 | voice_persist_service.py:129 | `Message.objects.filter(user_id,role="assistant",is_voice=True).values("request_id")` | 已回复子查询 | `_count_and_delete_excess`（sync，独立无外层事务） |
| 5 | voice_persist_service.py:130 | `Message.objects.filter(user_id,role="user",is_voice=True).exclude(request_id__in=Subquery)` +count+order+values_list | 找超限 record-only | 同上（sync 独立） |
| 6 | voice_persist_service.py:139 | `Message.objects.filter(message_id__in=oldest_ids).delete()` | 删超限 | 同上（sync 独立） |
| 7 | speaker_service.py:160 | `Message.objects.filter(speaker_id=lbl,is_voice=True).update(speaker_id=str(user_id),user_id=user_id)` | 追溯匹配改归属 | `_retrospective_match` 内 `sync_to_async(lambda)`（独立） |
| 8 | voice_pipeline.py:268 | `Message.objects.filter(request_id,user_id,role="user").update(content=text)` | 改 ambient 用户消息为 ASR 原文 | `sync_to_async(...update)`（独立） |

> 另有 line 85/95 的 `user_msg.save(update_fields=["is_voice"])` 为模型 `.save()`（非 `.objects`，rg 不命中）。是否一并收敛见 §7 待确认 2。

## 4. repo 方法映射（关键设计：sync vs async 二分）

**核心约束**：直连点 1/2/3 位于 `transaction.atomic()` 内的**同步**函数。repo 现有方法全部 `@sync_to_async`（异步）。
从同步事务块内不能 await 异步方法，且 `sync_to_async` 会切线程 → 破坏该事务的原子性。
因此 1/2/3 必须映射到**新增的同步 repo 方法**（普通函数，与调用方同线程共享事务）；4~8 独立，可映射到**异步** repo 方法。

### 4.1 chat/repositories.py — MessageRepository 新增

- 直连点 1、3 → 新增**同步** `get_by_request_id_sync(request_id, user_id, role="assistant") -> Optional[Message]`
  （语义与现有异步 `get_by_request_id` 完全一致，仅去掉 `@sync_to_async`）：
  ```python
  @staticmethod
  def get_by_request_id_sync(request_id, user_id, role="assistant"):
      qs = Message.objects.filter(request_id=request_id, user_id=user_id)
      if role is not None:
          qs = qs.filter(role=role)
      return qs.first()
  ```
- 直连点 4+5+6（一个内聚操作）→ 新增**异步** `delete_excess_record_only(user_id, limit) -> int`
  （整体搬 `_count_and_delete_excess` 的查询逻辑，逐行照搬 Subquery/exclude/count/order_by/values_list/delete，零行为变化）。
- 直连点 7 → 新增**异步** `reassign_speaker_messages(old_label, user_id) -> int`：
  ```python
  return Message.objects.filter(speaker_id=old_label, is_voice=True).update(
      speaker_id=str(user_id), user_id=user_id)
  ```
- 直连点 8 → 新增**异步** `update_content_by_request_id(request_id, user_id, content, role="user") -> int`：
  ```python
  return Message.objects.filter(request_id=request_id, user_id=user_id, role=role).update(content=content)
  ```

### 4.2 media/repositories.py — MediaAttachmentRepository 新增（scope 扩项）

- 直连点 2 → 新增**同步** `create_audio_attachment_sync(attachment_uuid, message, user_id, mime_type, file_name, file_size, storage_path, duration_seconds, created_at, expires_at) -> MediaAttachment`
  （内部 `MediaAttachment.objects.create(..., media_type=MediaAttachment.TYPE_AUDIO, ...)`，把 model+常量知识从 voice service 移出，逐字段照搬 82:130 当前 create 参数）。

## 5. 详细改动计划

### 文件 1: voice_persist_service.py

#### 改动 1.1 — `_atomic_mark_voice`（line 76-95）
- 现状：sync 函数内 `Message.objects.filter(...).first()`（82/92）+ `MediaAttachment.objects.create(...)`（86-91）+ 内联 `from apps.media.models import MediaAttachment`。
- 改为：调 `message_repo.get_by_request_id_sync(request_id, user_id, role="user"/"assistant")`
  与 `media_attachment_repo.create_audio_attachment_sync(...)`；删除 MediaAttachment 内联 import。
  `user_msg.is_voice=True; user_msg.save(update_fields=["is_voice"])` 是否收敛见 §7。
- 理由：分层红线 5；事务保持在 service，repo 提供同线程 sync 方法。
- 预估：+4 -12

#### 改动 1.2 — `_count_and_delete_excess`（line 125-140）
- 改为：函数体替换为 `return await message_repo.delete_excess_record_only(user_id, limit)`；
  该函数从 `@sync_to_async` 改为 `async def`（因 repo 方法已是异步）。调用方 `_cleanup_record_only` 已 await，无需改。
- 删除内联 `from django.db.models import Subquery`（移入 repo）。
- 预估：+2 -12

### 文件 2: voice_pipeline.py（line 262-271）
- 改动 2.1：直连点 8。删除内联 `from apps.chat.models import Message` + `sync_to_async(...filter...update)`，
  改为 `await message_repo.update_content_by_request_id(request_id, user_id, text, role="user")`。
  顶部若无 `message_repo` import 需补（core import，非函数内 lazy）。
- 预估：+2 -6

### 文件 3: speaker_service.py（line 143-169）
- 改动 3.1：直连点 7。删除内联 `from apps.chat.models import Message` 与 `sync_to_async(lambda...)`，
  改为 `count = await message_repo.reassign_speaker_messages(label, user_id)`。
- 预估：+2 -6

### 文件 4: chat/repositories.py
- 新增 §4.1 四个方法（1 sync + 3 async）。放在 MessageRepository 内相应位置。预估 +30。

### 文件 5: media/repositories.py
- 新增 §4.2 一个 sync 方法。预估 +12。

### 文件 6: test_voice_persist_service.py
- `TestAtomicMarkVoice` / `TestCountAndDeleteExcess` 仍调私有方法验证 DB 效果，行为不变应继续通过（回归护栏）。
- 新增：`message_repo.delete_excess_record_only` / `reassign_speaker_messages` / `update_content_by_request_id` /
  `get_by_request_id_sync` 与 `media_attachment_repo.create_audio_attachment_sync` 的直接单测（等价语义断言）。预估 +30。

## 6. 调查步骤（已完成，结论前置）

- [x] rg 核实 8 处直连点全部命中（§3 表），与 diag-20260717 一致。
- [x] 语义比对：1/3 等价现有 `get_by_request_id`；4/5/6 为内聚删除操作；7=update 归属；8=update content。
- [x] **investigation #2 结论**：`MediaAttachment.objects.create` 归 **media_attachment_repo**（media app 模型），非 message_repo。
- [x] **关键发现**：1/2/3 处于 `transaction.atomic()` 同步块 → 必须用 **sync repo 方法**（不能异步，否则破坏事务原子性）。此为本 batch 主要复杂度。

## 7. 验证计划

### 7.1 自动化
- [ ] `source /home/dantsinghua/work/linchat/linchat/bin/activate`
- [ ] `pytest backend/tests/voice/ -v`（重点 test_voice_persist_service / test_speaker_service / test_voice_pipeline）
- [ ] `pytest backend/apps/chat/ backend/apps/media/ -v`（repo 新方法回归）
- [ ] `rg -n 'Message\.objects|MediaAttachment\.objects' backend/apps/voice/services/ -g '!**/__pycache__/**'` → **应 0 命中**（metric：8→0）
- [ ] `ruff check backend/apps/voice/services/ backend/apps/chat/repositories.py backend/apps/media/repositories.py`

### 7.2 手动验证
- [ ] 触发 ambient 语音持久化：确认 Message.is_voice 标记 + MediaAttachment 音频写入 + record-only 超限清理行为不变。
- [ ] 声纹注册后追溯匹配：未知 speaker 历史消息正确改归属（§3 直连点 7）。

### 7.3 回归
- [ ] `pytest backend/apps/chat/ backend/apps/graph/ -v`（message_repo 被 chat/graph 复用，防跨 app 破坏）。

## 8. 回滚策略

`git revert <commit>`（04-plan 指定）。因本 batch 纯分层收敛且集中于新增 repo 方法 + 调用点替换，
单 commit revert 即可完全还原，无 schema/迁移，无需 worktree 级操作。

## 9. ⚠️ 需要安琳确认的事项

- [ ] **待确认 1（scope 扩项）**：`MediaAttachment.objects.create`（直连点 2）按分层应落在
      `backend/apps/media/repositories.py`（media app），需触碰该文件——04-plan 的 files_touched 未列它，实际由 5 文件变 6 文件。是否批准？
- [ ] **待确认 2（收敛边界）**：line 85/95 的 `msg.is_voice=True; msg.save(update_fields=["is_voice"])`
      是模型 `.save()`（非 `.objects`，不计入 metric）。是否也收敛为 repo 同步方法（如 `set_voice_flag_sync`）？
      不收敛则 §2 metric 仍达标（8→0），但 service 层残留 `.save()`。建议一并收敛保持一致，需你拍板。
- [ ] **待确认 3（PD-4 拍板）**：本 batch 默认采纳 PD-4 "收敛 message_repo" 方案（与 ambient_light 一致）。notes 要求落地前你对 PD-4 拍板确认。
- [ ] **硬限制提示**：`voice_pipeline.py` 当前 329 行（>300 硬限制）。本 batch 仅微减（约 -4 行），**不**在本 batch 拆分（拆分属另一 batch，避免扩大 high-risk 改动面）。是否接受本 batch 暂不处理其 300 行超标？
- [ ] **风险提示**：voice 为高活跃高风险区；sync/async 二分（§4）若实现有误可能破坏 `_atomic_mark_voice` 事务原子性或引入跨线程事务 bug。执行阶段务必带回归测试先行。

## 10. 执行预算

- 预计 tool calls：约 20-30（6 文件精修 + 多轮 pytest）
- 预计 token：中等
- 预计 session：1（与 04-plan estimated_sessions 一致，未超 2×）
