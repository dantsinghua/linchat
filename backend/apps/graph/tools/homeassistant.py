import logging
from typing import Any

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool

from apps.graph.tools.ha_client import (
    HAAuthError, HAConnectionError, HAError, HANotFoundError, HAClient,
)
from apps.graph.tools.ha_helpers import (  # noqa: E501
    ACTION_DESC, RATE_LIMITS, _check_rate_limit, _diagnose_automations, _diagnose_device,
    _diagnose_error_log, _diagnose_health, _diagnose_offline_scan, _format_control_result,
    _format_device_list, _format_history, _format_state, _get_user_id, _is_blocked, _is_sensitive,
)

logger = logging.getLogger(__name__)

ACTION_MAP: dict[str, tuple[str, str]] = {
    "turn_on": ("homeassistant", "turn_on"), "turn_off": ("homeassistant", "turn_off"),
    "toggle": ("homeassistant", "toggle"), "set_brightness": ("light", "turn_on"),
    "set_color": ("light", "turn_on"), "set_color_temp": ("light", "turn_on"),
    "set_temperature": ("climate", "set_temperature"), "set_hvac_mode": ("climate", "set_hvac_mode"),
    "set_fan_speed": ("fan", "set_percentage"), "play": ("media_player", "media_play"),
    "pause": ("media_player", "media_pause"), "volume": ("media_player", "volume_set"),
    "scene": ("scene", "turn_on"), "script": ("script", "turn_on"),
    "lock": ("lock", "lock"), "unlock": ("lock", "unlock"),
    "open_cover": ("cover", "open_cover"), "close_cover": ("cover", "close_cover"),
}

_HA_ERRORS = {
    HAAuthError: "HA 认证失败，请检查 Token 配置",
    HANotFoundError: "未找到设备 {eid}",
    HAConnectionError: "Home Assistant 服务不可达，请检查网络连接",
}


def _handle_ha_error(e: Exception, eid: str = "", tool_name: str = "") -> str:
    for exc_type, msg in _HA_ERRORS.items():
        if isinstance(e, exc_type): return msg.format(eid=eid)
    if isinstance(e, HAError): return f"HA 服务错误: {e}"
    logger.exception(f"{tool_name} unexpected error")
    return f"{tool_name}时发生错误: {e}"


@tool
async def ha_control(
    entity_id: str, action: str, params: dict[str, Any] | None = None,
    config: RunnableConfig = None,
) -> str:
    """控制 Home Assistant 设备。
    Args:
        entity_id: 设备实体ID，如 light.living_room, switch.kitchen
        action: turn_on / turn_off / toggle / set_temperature / set_brightness 等
        params: 附加参数，如 {"brightness": 128}
        config: LangChain 运行配置（自动注入）"""
    try:
        user_id = _get_user_id(config)
    except ValueError as e:
        return f"错误: {e}"
    rate_err = await _check_rate_limit(user_id, "control")
    if rate_err: return rate_err
    if _is_blocked(entity_id): return f"该设备 ({entity_id}) 已被禁止通过聊天控制"
    is_sens, confirm_msg = _is_sensitive(action, entity_id)
    if is_sens: return confirm_msg
    if action not in ACTION_MAP:
        return f"不支持的操作: {action}\n可用操作: {', '.join(sorted(ACTION_MAP.keys()))}"
    domain, service = ACTION_MAP[action]
    service_data = {"entity_id": entity_id, **(params or {})}
    try:
        result = await HAClient().call_service(domain, service, service_data)
        return _format_control_result(action, entity_id, result)
    except Exception as e:
        return _handle_ha_error(e, entity_id, "控制设备")


@tool
async def ha_query(
    query_type: str, entity_id: str | None = None, domain: str | None = None,
    hours: int = 24, config: RunnableConfig = None,
) -> str:
    """查询 Home Assistant 设备状态。
    Args:
        query_type: state / list / history
        entity_id: 设备实体ID（state/history 时必填）
        domain: 设备域（list 时可选，如 light / switch / climate / sensor）
        hours: 历史查询时间范围，默认24小时
        config: LangChain 运行配置（自动注入）"""
    try:
        user_id = _get_user_id(config)
    except ValueError as e:
        return f"错误: {e}"
    rate_err = await _check_rate_limit(user_id, "query")
    if rate_err: return rate_err
    client = HAClient()
    try:
        if query_type == "state":
            if not entity_id: return "查询单设备状态需要提供 entity_id"
            return _format_state(await client.get_state(entity_id))
        elif query_type == "list":
            return _format_device_list(await client.get_states(domain=domain), domain)
        elif query_type == "history":
            if not entity_id: return "查询历史记录需要提供 entity_id"
            return _format_history(await client.get_history(entity_id, hours=hours), entity_id, hours)
        else:
            return f"不支持的查询类型: {query_type}，可用类型: state / list / history"
    except Exception as e:
        return _handle_ha_error(e, entity_id or "", "查询")


DIAG_DISPATCH = {
    "health": lambda c, _: _diagnose_health(c),
    "device": lambda c, eid: _diagnose_device(c, eid) if eid else "单设备诊断需要提供 entity_id",
    "offline_scan": lambda c, _: _diagnose_offline_scan(c),
    "automations": lambda c, _: _diagnose_automations(c),
    "error_log": lambda c, _: _diagnose_error_log(c),
}


@tool
async def ha_diagnose(
    diagnose_type: str, entity_id: str | None = None, config: RunnableConfig = None,
) -> str:
    """诊断 Home Assistant 设备或系统问题。"""
    try:
        user_id = _get_user_id(config)
    except ValueError as e:
        return f"错误: {e}"
    rate_err = await _check_rate_limit(user_id, "diagnose")
    if rate_err: return rate_err
    handler = DIAG_DISPATCH.get(diagnose_type)
    if not handler:
        return f"不支持的诊断类型: {diagnose_type}\n可用类型: health / device / offline_scan / automations / error_log"
    try:
        result = handler(HAClient(), entity_id)
        return await result if hasattr(result, "__await__") else result
    except Exception as e:
        return _handle_ha_error(e, entity_id or "", "诊断")

HA_TOOLS = [ha_query, ha_control, ha_diagnose]
