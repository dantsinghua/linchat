"""记忆 REST API 视图 — 仅处理 HTTP 请求响应，业务逻辑委托 MemoryService"""

from asgiref.sync import async_to_sync
from rest_framework.decorators import api_view
from rest_framework.request import Request
from rest_framework.response import Response

from apps.common.responses import ApiResponse
from apps.memory.serializers import (
    MemoryCreateSerializer, MemoryListQuerySerializer, MemoryResponseSerializer,
    MemorySearchSerializer, MemoryUpdateSerializer,
)
from apps.memory.services import MemoryNotFoundError, MemoryService


@api_view(["GET", "POST"])
def memory_list_create(request: Request) -> Response:
    user_id = request.user_id

    if request.method == "GET":
        qs = MemoryListQuerySerializer(data=request.query_params)
        if not qs.is_valid():
            return ApiResponse.validation_error(errors=qs.errors)
        d = qs.validated_data
        memories, total = async_to_sync(MemoryService.list_memories)(
            user_id=user_id, type_filter=d.get("type"),
            page=d.get("page", 1), page_size=d.get("page_size", 20),
        )
        return ApiResponse.paginated(
            items=MemoryResponseSerializer(memories, many=True).data,
            total=total, page=d.get("page", 1), page_size=d.get("page_size", 20),
        )

    # POST
    s = MemoryCreateSerializer(data=request.data)
    if not s.is_valid():
        return ApiResponse.validation_error(errors=s.errors)
    memory = async_to_sync(MemoryService.create_memory)(
        user_id=user_id, content=s.validated_data["content"], name=s.validated_data.get("name"),
    )
    return ApiResponse.created(data=MemoryResponseSerializer(memory).data)


@api_view(["GET", "PUT", "DELETE"])
def memory_detail(request: Request, memory_id: int) -> Response:
    user_id = request.user_id

    try:
        if request.method == "GET":
            memory = async_to_sync(MemoryService.get_memory)(memory_id=memory_id, user_id=user_id)
            return ApiResponse.success(data=MemoryResponseSerializer(memory).data)

        if request.method == "PUT":
            s = MemoryUpdateSerializer(data=request.data)
            if not s.is_valid():
                return ApiResponse.validation_error(errors=s.errors)
            memory = async_to_sync(MemoryService.update_memory)(
                memory_id=memory_id, user_id=user_id, content=s.validated_data["content"],
            )
            return ApiResponse.success(data=MemoryResponseSerializer(memory).data)

        # DELETE
        async_to_sync(MemoryService.delete_memory)(memory_id=memory_id, user_id=user_id)
        return ApiResponse.success(message="删除成功")
    except MemoryNotFoundError:
        return ApiResponse.not_found(message="记忆不存在")


@api_view(["POST"])
def memory_search(request: Request) -> Response:
    s = MemorySearchSerializer(data=request.data)
    if not s.is_valid():
        return ApiResponse.validation_error(errors=s.errors)

    results = async_to_sync(MemoryService.search_memory)(
        user_id=request.user_id, query=s.validated_data["query"],
        limit=s.validated_data.get("limit", 5),
    )
    items = []
    for item in results:
        data = MemoryResponseSerializer(item["memory"]).data
        data["score"] = item["score"]
        data["match_type"] = item["match_type"]
        items.append(data)
    return ApiResponse.success(data=items)
