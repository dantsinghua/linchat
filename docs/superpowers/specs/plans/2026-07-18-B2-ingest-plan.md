# B2 执行计划 — 摄入通道（LinChat + wechat）

> 由 init-b2 产出、主 agent 自审通过（3 项关键校正见下）。executor 严格照做。

## 主 agent 校正确认（覆盖设计文档）
1. **不复用 create_memory**：它内部 `_dispatch_embedding`(services.py:60-66) 总调 `.delay()` → 撞 `has_active_users` 门禁(tasks.py:15)。→ **新增 `MemoryService.ingest_memory`**：同步 embed、name 幂等、embed 失败不阻断落库。
2. **daily/monthly-summary 天然不抓 wechat/oa**（已核查 tasks.py/task_helpers.py/services.py）→ 无需黑白名单，仅加 1 条回归测试锁死。
3. **PUBLIC_PATHS 安全红线**：`/api/v1/internal/` 跳过 cookie，view 必须对无/错 token 返回 401。

## executor 实施前必核实（行号/签名以实际代码为准）
- `apps/common/responses.py` 的 `ApiResponse.unauthorized/validation_error/success` 真实方法名与签名
- `apps/memory/repositories.py` 现有 create/update 方法名、`embedding_repo` 是否存在及方法（delete_by_memory_id/create）
- `core/urls.py` 的 include 结构、`apps/memory/models.py` MemoryType 真实行号、`EmbeddingStatus` 枚举名
- **msg_synth hook 接口匹配**：`/home/dantsinghua/clawd/scripts/wechat-narrator/msg_synth.py` 里 `import linchat_ingest_client` 后的实际调用签名，client 的 `ingest(day=, summary=, ...)` 必须与之一致（B1 已产出 msg_synth，以其为准）

## 分支/git 边界
- **LinChat 侧**：在集成分支 `batch/c1-oa-search` 上叠加（当前分支），validate_full 全绿才 commit（scope=memory）。
- **wechat 侧**（linchat_ingest_client.py + test）：clawd 仓库，**不 git**（交安琳），只改文件 + unittest。

---

## 1. memory type 枚举（`apps/memory/models.py:10-14` MemoryType 追加）
```python
        WECHAT = "wechat", "微信对话"
        OA = "oa", "公众号"
```
无 migration（choices 变更不生成 schema migration）。

## 2. daily-summary 隔离 — 仅加回归测试（§6.2），代码不改

## 3. 内部摄入端点（LinChat）

### 3.1 `apps/common/middleware.py:18` PUBLIC_PATHS 加前缀 `/api/v1/internal/`（一并覆盖 C2 husband 端点）

### 3.2 `apps/memory/serializers.py` 追加（不动 MemoryCreateSerializer）
```python
class InternalIngestSerializer(serializers.Serializer):
    content = serializers.CharField(max_length=settings.MEMORY_CONTENT_MAX_LENGTH)
    name = serializers.CharField(max_length=200)
    tag = serializers.CharField(max_length=100, required=False, allow_null=True, default=None)
    source = serializers.ChoiceField(choices=["wechat", "oa"], default="wechat")
```

### 3.3 `apps/memory/services.py` 加 `MemoryService.ingest_memory`（同步 embed、绕 celery 门禁、name 幂等）
```python
    @staticmethod
    async def ingest_memory(user_id: int, content: str, name: str,
                            source: str = "wechat", tag: Optional[str] = None) -> tuple[UserMemory, bool]:
        """内部摄入：name 作自然幂等键；同步生成 embedding（不走 has_active_users 门禁的 celery）。
        返回 (memory, deduped)。embed 失败不阻断落库（status=FAILED，health_check 后续重试）。"""
        existing = await memory_repo.get_by_type_and_name(user_id, source, name)
        deduped = existing is not None
        if existing:
            existing.content = content
            if tag is not None: existing.tags = [tag]
            existing.embedding_status = UserMemory.EmbeddingStatus.PENDING; existing.retry_count = 0
            memory = await memory_repo.update(existing)
        else:
            memory = await memory_repo.create(UserMemory(
                user_id=user_id, content=content, name=name, type=source,
                embedding_status=UserMemory.EmbeddingStatus.PENDING, retry_count=0,
                tags=[tag] if tag else None))
        try:
            vec = await EmbeddingClient.generate_embedding(content)
            await embedding_repo.delete_by_memory_id(memory.id)
            await embedding_repo.create(UserMemoryEmbedding(
                memory=memory, user_id=user_id, type=source, name=name,
                chunk_index=0, chunk_text=content, embedding=vec))
            memory.embedding_status = UserMemory.EmbeddingStatus.DONE
            memory = await memory_repo.update(memory)
        except Exception as e:
            logger.warning("Ingest embedding failed (memory_id=%s): %s", memory.id, e)
            memory.embedding_status = UserMemory.EmbeddingStatus.FAILED; memory.retry_count += 1
            await memory_repo.update(memory)
        return memory, deduped
```
import 补 `UserMemoryEmbedding`。**若 embedding_repo/repo 方法名与上不符，用实际存在的等价方法**（executor 核实 repositories.py 后适配）。

### 3.4 `apps/memory/repositories.py` 加 `MemoryRepository.get_by_type_and_name`
```python
    @staticmethod
    @sync_to_async
    def get_by_type_and_name(user_id: int, type: str, name: str) -> Optional[UserMemory]:
        _require_user_id(user_id)
        return UserMemory.objects.filter(user_id=user_id, type=type, name=name).order_by("-created_at").first()
```

### 3.5 新建 `apps/memory/internal_views.py`（api_view，设备 token 鉴权，无/错 token 返回 401）
```python
"""内部端点（设备 token 鉴权，跳过 cookie 中间件）。不属对外 API 契约。"""
import logging
from asgiref.sync import async_to_sync
from rest_framework.decorators import api_view
from rest_framework.request import Request
from rest_framework.response import Response
from apps.common.responses import ApiResponse
from apps.memory.serializers import InternalIngestSerializer
from apps.memory.services import MemoryService
from apps.voice.services.device_service import device_service

logger = logging.getLogger(__name__)

@api_view(["POST"])
def internal_ingest(request: Request) -> Response:
    token = request.META.get("HTTP_X_DEVICE_TOKEN", "")
    auth = async_to_sync(device_service.authenticate_by_token)(token)
    if not auth:
        return ApiResponse.unauthorized(message="设备 token 无效")
    user_id = auth["user_id"]
    s = InternalIngestSerializer(data=request.data)
    if not s.is_valid():
        return ApiResponse.validation_error(errors=s.errors)
    d = s.validated_data
    memory, deduped = async_to_sync(MemoryService.ingest_memory)(
        user_id=user_id, content=d["content"], name=d["name"],
        source=d["source"], tag=d.get("tag"))
    logger.info("Internal ingest: user_id=%s type=%s name=%s deduped=%s status=%s",
                user_id, d["source"], d["name"], deduped, memory.embedding_status)
    return ApiResponse.success(data={
        "id": memory.id, "type": memory.type, "name": memory.name,
        "embedding_status": memory.embedding_status, "deduped": deduped}, message="摄入成功")
```
（`ApiResponse` 方法名以 responses.py 实际为准；无对应 unauthorized 则用 success(code=401)/等价。）

### 3.6 路由：新建 `apps/memory/internal_urls.py` + `core/urls.py` 加 `path("internal/", include("apps.memory.internal_urls"))` → **POST /api/v1/internal/ingest/**

## 4. `linchat_ingest_client.py`（wechat 侧，系统 python3 纯 stdlib，不 git）
契约：msg_synth 调 `ingest(day=, summary=)`；**入本地 pending(sqlite,content_hash UNIQUE) 即成功返回不抛**，再尽力 flush(POST)；LinChat 宕机条目留 pending，下次 ingest 或 `--flush` 补投。只有本地 pending 写失败才抛（→ msg_synth 降级不 mark）。完整代码见 init-b2 §4（enqueue/flush_pending/ingest/_post，env: LINCHAT_BASE_URL/LINCHAT_DEVICE_TOKEN/WN_INGEST_DB）。**实施时先读 msg_synth.py 确认调用签名一致**。

## 5. 设备 token 注册（交安琳执行，executor 不做）
```bash
source /home/dantsinghua/work/linchat/linchat/bin/activate && cd /home/dantsinghua/work/linchat/backend
python manage.py shell -c "import asyncio; from apps.voice.services.device_service import device_service; print(asyncio.run(device_service.register_device(user_id=7, name='wechat-narrator')))"
```
明文 token 写 `~/.wechat-narrator/ingest.env`：`LINCHAT_BASE_URL=http://127.0.0.1:8002` + `LINCHAT_DEVICE_TOKEN=<明文>`

## 6. 测试

### 6.1 LinChat pytest `tests/memory/test_internal_ingest.py`（沿用 test_views.py 的 APIClient + django_db）
mock `authenticate_by_token`(AsyncMock) + `EmbeddingClient.generate_embedding`(AsyncMock 1024维)：
1. valid_token → 201, UserMemory(type=wechat,name=...) embedding_status=done + 1 条 embedding
2. invalid_token → 401 无落库　3. missing_token → 401
4. idempotent 同 name 二次 → deduped=True, UserMemory 仍1条(内容更新), embedding 仍1条
5. embed_failure → 仍201 status=failed, memory 存在
6. bypasses_active_users_gate → patch `apps.memory.tasks.generate_embedding` 断言 `.delay.assert_not_called()`
7. source_oa → type=oa

### 6.2 daily-summary 隔离回归 `tests/memory/test_tasks.py` 加
8. daily_summary_excludes_wechat_type → 造 UserMemory(type=wechat)+Message，跑 collect_content(7,COMPACTION) → 内容不含 wechat 文本

### 6.3 wechat unittest `scripts/wechat-narrator/tests/test_ingest_client.py`（临时 db + mock urlopen）
1. enqueues_and_flushes(mock 201→pending清空,body含name+X-Device-Token)　2. idempotent_hash　3. retries_then_keeps_on_down(URLError→ingest不抛,pending保留,恢复后flush清空)　4. empty_summary_noop　5. local_persist_failure_raises

## 7. 验证（executor 必做）
1. `bash refactor/loop/validate_full.sh` 全绿，`rc=0 && failed=0`（应 ≥1785 passed，新增 ~8 用例）
2. wechat：`cd /home/dantsinghua/clawd/scripts/wechat-narrator && python3 -m unittest tests.test_ingest_client -v` 全绿
3. **不做真实 HTTP 冒烟**（需真 token + 在线），把 curl 冒烟命令留报告里交安琳
4. LinChat 全绿才 `git add`（仅 LinChat 侧文件）+ commit（集成分支 batch/c1-oa-search）；wechat 侧不 git

## 红线合规
对外 API 契约零变更（MemoryCreateSerializer/memory 路由不动）；无 migration；不裸 SQL(走 ORM)；隔离粒度 user_id；PUBLIC_PATHS 下 view 强制 token 校验(401)；wechat 侧纯 stdlib 无新依赖。
