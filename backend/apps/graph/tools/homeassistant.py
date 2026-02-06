"""Home Assistant 工具集 — ha_query / ha_control / ha_diagnose

SubAgent 内部工具，主 agent 不直接调用。
参考: specs/007-home-assistant-tools/, M2b-home-assistant-requirements.md
"""

import fnmatch
import logging
from typing import Any

import redis.asyncio as aioredis
from django.conf import settings
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool

from apps.graph.tools.ha_client import (
    HAClient,
    HAAuthError,
    HAConnectionError,
    HAError,
    HANotFoundError,
)

logger = logging.getLogger(__name__)


def _cap_result(text: str, tool_name: str) -> str:
    """延迟导入 cap_tool_result 避免循环导入"""
    from apps.graph.tools import cap_tool_result

    return cap_tool_result(text, tool_name)


# ============ 辅助函数 ============


def _get_user_id(config: RunnableConfig | None) -> int:
    """从 RunnableConfig 提取 user_id"""
    if config is None:
        raise ValueError("config is required for HA tools")
    user_id = config.get("configurable", {}).get("user_id")
    if user_id is None:
        raise ValueError("user_id not found in RunnableConfig")
    return int(user_id)


# ============ T008: 速率限制 ============


RATE_LIMITS = {
    "control": 10,  # 10 次/分钟
    "query": 30,  # 30 次/分钟
    "diagnose": 5,  # 5 次/分钟
}


async def _check_rate_limit(user_id: int, tool_type: str) -> str | None:
    """检查速率限制

    Args:
        user_id: 用户ID
        tool_type: 工具类型 (control/query/diagnose)

    Returns:
        None 表示通过，否则返回错误消息
    """
    limit = RATE_LIMITS.get(tool_type, 10)
    redis_key = f"ha:{tool_type}:rate:{user_id}"

    r: aioredis.Redis = aioredis.from_url(settings.REDIS_URL)
    try:
        count = await r.incr(redis_key)
        if count == 1:
            await r.expire(redis_key, 60)  # TTL 60s
        if count > limit:
            return f"操作过于频繁，请稍后再试（{tool_type} 限制 {limit} 次/分钟）"
        return None
    finally:
        await r.aclose()


# ============ T009: 黑名单检查 ============


def _is_blocked(entity_id: str) -> bool:
    """检查设备是否在黑名单中"""
    blocked = getattr(settings, "HA_BLOCKED_ENTITIES", [])
    return entity_id in blocked


# ============ T010: 敏感操作检测 ============


def _is_sensitive(action: str, entity_id: str) -> tuple[bool, str | None]:
    """检测敏感操作

    Returns:
        (is_sensitive, confirmation_message)
        如果是敏感操作，返回 (True, 确认提示)
        否则返回 (False, None)
    """
    # L3 敏感：unlock
    if action == "unlock":
        return (
            True,
            f"⚠️ 敏感操作确认\n"
            f"即将执行: 解锁 {entity_id}\n"
            f"这是一个涉及安全的操作，请确认是否继续。\n"
            f'回复"确认解锁"以执行，或"取消"以放弃。',
        )

    # L3 敏感：打开车库门 (cover.garage_*)
    if action == "open_cover" and fnmatch.fnmatch(entity_id, "cover.garage_*"):
        return (
            True,
            f"⚠️ 敏感操作确认\n"
            f"即将执行: 打开车库门 {entity_id}\n"
            f"这是一个涉及安全的操作，请确认是否继续。\n"
            f'回复"确认打开"以执行，或"取消"以放弃。',
        )

    # L4 危险：禁用自动化规则
    if action == "turn_off" and fnmatch.fnmatch(entity_id, "automation.*"):
        return (
            True,
            f"⚠️ 危险操作确认\n"
            f"即将执行: 禁用自动化规则 {entity_id}\n"
            f"禁用自动化可能影响系统正常运行，请确认是否继续。\n"
            f'回复"确认禁用"以执行，或"取消"以放弃。',
        )

    return (False, None)


# ============ T011: ACTION_MAP 和 ha_control ============

ACTION_MAP: dict[str, tuple[str, str]] = {
    # action -> (domain, service)
    "turn_on": ("homeassistant", "turn_on"),
    "turn_off": ("homeassistant", "turn_off"),
    "toggle": ("homeassistant", "toggle"),
    "set_brightness": ("light", "turn_on"),
    "set_color": ("light", "turn_on"),
    "set_color_temp": ("light", "turn_on"),
    "set_temperature": ("climate", "set_temperature"),
    "set_hvac_mode": ("climate", "set_hvac_mode"),
    "set_fan_speed": ("fan", "set_percentage"),
    "play": ("media_player", "media_play"),
    "pause": ("media_player", "media_pause"),
    "volume": ("media_player", "volume_set"),
    "scene": ("scene", "turn_on"),
    "script": ("script", "turn_on"),
    "lock": ("lock", "lock"),
    "unlock": ("lock", "unlock"),
    "open_cover": ("cover", "open_cover"),
    "close_cover": ("cover", "close_cover"),
}


@tool
async def ha_control(
    entity_id: str,
    action: str,
    params: dict[str, Any] | None = None,
    config: RunnableConfig = None,
) -> str:
    """控制 Home Assistant 设备。

    Args:
        entity_id: 设备实体ID，如 light.living_room, switch.kitchen
        action: 操作类型: turn_on / turn_off / toggle / set_temperature / set_brightness 等
        params: 附加参数，如 {"brightness": 128, "color_temp_kelvin": 4000}
        config: LangChain 运行配置（自动注入）
    """
    try:
        user_id = _get_user_id(config)
    except ValueError as e:
        return f"错误: {e}"

    # 1. 速率限制检查
    rate_err = await _check_rate_limit(user_id, "control")
    if rate_err:
        return rate_err

    # 2. 黑名单检查
    if _is_blocked(entity_id):
        return f"该设备 ({entity_id}) 已被禁止通过聊天控制"

    # 3. 敏感操作检测
    is_sensitive, confirm_msg = _is_sensitive(action, entity_id)
    if is_sensitive:
        return confirm_msg

    # 4. ACTION_MAP 查找
    if action not in ACTION_MAP:
        available = ", ".join(sorted(ACTION_MAP.keys()))
        return f"不支持的操作: {action}\n可用操作: {available}"

    domain, service = ACTION_MAP[action]

    # 5. 构建服务数据
    service_data = {"entity_id": entity_id}
    if params:
        service_data.update(params)

    # 6. 调用 HAClient
    try:
        client = HAClient()
        result = await client.call_service(domain, service, service_data)

        # 格式化返回
        action_desc = {
            "turn_on": "开启",
            "turn_off": "关闭",
            "toggle": "切换",
            "set_brightness": "调节亮度",
            "set_color": "设置颜色",
            "set_color_temp": "设置色温",
            "set_temperature": "设置温度",
            "set_hvac_mode": "设置模式",
            "set_fan_speed": "设置风速",
            "play": "播放",
            "pause": "暂停",
            "volume": "调节音量",
            "scene": "激活场景",
            "script": "执行脚本",
            "lock": "锁定",
            "unlock": "解锁",
            "open_cover": "打开",
            "close_cover": "关闭",
        }.get(action, action)

        # 获取设备友好名称
        friendly_name = entity_id
        if result and len(result) > 0:
            attrs = result[0].get("attributes", {})
            friendly_name = attrs.get("friendly_name", entity_id)
            current_state = result[0].get("state", "unknown")

            # 构建状态详情
            state_detail = f"当前状态: {current_state}"
            if "brightness" in attrs:
                pct = round(attrs["brightness"] / 255 * 100)
                state_detail += f", 亮度 {attrs['brightness']}/255 ({pct}%)"
            if "temperature" in attrs:
                state_detail += f", 温度 {attrs['temperature']}°C"

            return f"✅ 已执行: {action_desc} {friendly_name} ({entity_id})\n{state_detail}"

        return f"✅ 操作已发送: {action_desc} {entity_id}"

    except HAAuthError:
        return "HA 认证失败，请检查 Token 配置"
    except HANotFoundError:
        return f"未找到设备 {entity_id}，可用 ha_query(query_type='list') 查看设备列表"
    except HAConnectionError:
        return "Home Assistant 服务不可达，请检查网络连接"
    except HAError as e:
        return f"HA 服务错误: {e}"
    except Exception as e:
        logger.exception("ha_control unexpected error")
        return f"控制设备时发生错误: {e}"


# ============ T013: ha_query ============


@tool
async def ha_query(
    query_type: str,
    entity_id: str | None = None,
    domain: str | None = None,
    hours: int = 24,
    config: RunnableConfig = None,
) -> str:
    """查询 Home Assistant 设备状态。

    Args:
        query_type: 查询类型: state / list / history
        entity_id: 设备实体ID（state/history 时必填）
        domain: 设备域（list 时可选，如 light / switch / climate / sensor）
        hours: 历史查询时间范围，默认24小时
        config: LangChain 运行配置（自动注入）
    """
    try:
        user_id = _get_user_id(config)
    except ValueError as e:
        return f"错误: {e}"

    # 速率限制检查
    rate_err = await _check_rate_limit(user_id, "query")
    if rate_err:
        return rate_err

    client = HAClient()

    try:
        if query_type == "state":
            if not entity_id:
                return "查询单设备状态需要提供 entity_id"

            state = await client.get_state(entity_id)
            return _format_state(state)

        elif query_type == "list":
            states = await client.get_states(domain=domain)
            return _format_device_list(states, domain)

        elif query_type == "history":
            if not entity_id:
                return "查询历史记录需要提供 entity_id"

            history = await client.get_history(entity_id, hours=hours)
            return _format_history(history, entity_id, hours)

        else:
            return f"不支持的查询类型: {query_type}，可用类型: state / list / history"

    except HAAuthError:
        return "HA 认证失败，请检查 Token 配置"
    except HANotFoundError:
        return f"未找到设备 {entity_id}"
    except HAConnectionError:
        return "Home Assistant 服务不可达，请检查网络连接"
    except HAError as e:
        return f"HA 服务错误: {e}"
    except Exception as e:
        logger.exception("ha_query unexpected error")
        return f"查询时发生错误: {e}"


def _format_state(state: dict) -> str:
    """格式化单设备状态"""
    entity_id = state.get("entity_id", "unknown")
    attrs = state.get("attributes", {})
    friendly_name = attrs.get("friendly_name", entity_id)
    current_state = state.get("state", "unknown")
    last_changed = state.get("last_changed", "")

    lines = [
        f"# 设备状态: {entity_id}",
        f"- 名称: {friendly_name}",
        f"- 状态: {current_state}",
    ]

    # 添加常见属性
    if "brightness" in attrs:
        pct = round(attrs["brightness"] / 255 * 100)
        lines.append(f"- 亮度: {attrs['brightness']}/255 ({pct}%)")
    if "color_temp_kelvin" in attrs:
        lines.append(f"- 色温: {attrs['color_temp_kelvin']}K")
    if "temperature" in attrs:
        lines.append(f"- 温度: {attrs['temperature']}°C")
    if "current_temperature" in attrs:
        lines.append(f"- 当前温度: {attrs['current_temperature']}°C")
    if "hvac_mode" in attrs:
        lines.append(f"- 模式: {attrs['hvac_mode']}")
    if "volume_level" in attrs:
        lines.append(f"- 音量: {int(attrs['volume_level'] * 100)}%")

    if last_changed:
        # 简化时间格式
        lines.append(f"- 最后变更: {last_changed[:19].replace('T', ' ')}")

    return _cap_result("\n".join(lines), "ha_query")


def _format_device_list(states: list[dict], domain: str | None) -> str:
    """格式化设备列表，按域分组"""
    if not states:
        return f"未找到设备" + (f"（域: {domain}）" if domain else "")

    # 按域分组
    grouped: dict[str, list[dict]] = {}
    for s in states:
        d = s["entity_id"].split(".")[0]
        grouped.setdefault(d, []).append(s)

    lines = []
    title = f"# 设备列表" + (f" (域: {domain})" if domain else "")
    lines.append(title)

    for d, devices in sorted(grouped.items()):
        lines.append(f"\n## {d} ({len(devices)} 个)")

        # 每域最多显示 20 个
        display_count = min(len(devices), 20)
        for i, dev in enumerate(devices[:display_count], 1):
            eid = dev["entity_id"]
            fname = dev.get("attributes", {}).get("friendly_name", eid)
            st = dev.get("state", "unknown")

            # 简化状态显示
            state_info = st
            attrs = dev.get("attributes", {})
            if "brightness" in attrs:
                pct = round(attrs["brightness"] / 255 * 100)
                state_info = f"{st} ({pct}%)"

            lines.append(f"{i}. {eid} — {fname} — {state_info}")

        if len(devices) > 20:
            lines.append(f"... 及其他 {len(devices) - 20} 个")

    # 统计信息
    total = len(states)
    on_count = sum(1 for s in states if s.get("state") in ("on", "playing", "open"))
    lines.append(f"\n共 {total} 个设备，{on_count} 个开启/活跃")

    return _cap_result("\n".join(lines), "ha_query")


def _format_history(history: list[list[dict]], entity_id: str, hours: int) -> str:
    """格式化历史记录"""
    if not history or not history[0]:
        return f"设备 {entity_id} 在过去 {hours} 小时内无状态变化记录"

    changes = history[0]
    lines = [f"# 历史记录: {entity_id} (过去 {hours} 小时)"]

    for i, change in enumerate(changes[-20:], 1):  # 最多显示最近 20 条
        state = change.get("state", "unknown")
        last_changed = change.get("last_changed", "")
        time_str = last_changed[:19].replace("T", " ") if last_changed else ""
        lines.append(f"{i}. {time_str} → {state}")

    if len(changes) > 20:
        lines.append(f"... 共 {len(changes)} 条记录")

    return _cap_result("\n".join(lines), "ha_query")


# ============ T015: ha_diagnose ============


@tool
async def ha_diagnose(
    diagnose_type: str,
    entity_id: str | None = None,
    config: RunnableConfig = None,
) -> str:
    """诊断 Home Assistant 设备或系统问题。

    Args:
        diagnose_type: 诊断类型:
            - health: 系统健康检查（版本、运行状态）
            - device: 单设备诊断（可达性、最近状态变化）
            - offline_scan: 扫描所有不可达设备
            - automations: 检查自动化规则状态
            - error_log: 获取最近错误日志
        entity_id: 设备诊断时必填
        config: LangChain 运行配置（自动注入）
    """
    try:
        user_id = _get_user_id(config)
    except ValueError as e:
        return f"错误: {e}"

    # 速率限制检查
    rate_err = await _check_rate_limit(user_id, "diagnose")
    if rate_err:
        return rate_err

    client = HAClient()

    try:
        if diagnose_type == "health":
            return await _diagnose_health(client)

        elif diagnose_type == "device":
            if not entity_id:
                return "单设备诊断需要提供 entity_id"
            return await _diagnose_device(client, entity_id)

        elif diagnose_type == "offline_scan":
            return await _diagnose_offline_scan(client)

        elif diagnose_type == "automations":
            return await _diagnose_automations(client)

        elif diagnose_type == "error_log":
            return await _diagnose_error_log(client)

        else:
            return (
                f"不支持的诊断类型: {diagnose_type}\n"
                "可用类型: health / device / offline_scan / automations / error_log"
            )

    except HAAuthError:
        return "HA 认证失败，请检查 Token 配置"
    except HAConnectionError:
        return "Home Assistant 服务不可达，请检查网络连接"
    except HAError as e:
        return f"HA 服务错误: {e}"
    except Exception as e:
        logger.exception("ha_diagnose unexpected error")
        return f"诊断时发生错误: {e}"


async def _diagnose_health(client: HAClient) -> str:
    """系统健康检查"""
    healthy = await client.check_health()
    config = await client.get_config()

    version = config.get("version", "unknown")
    components = config.get("components", [])

    status = "正常 ✅" if healthy else "异常 ⚠️"

    lines = [
        "# 🏠 Home Assistant 系统状态",
        f"- 版本: {version}",
        f"- 组件数: {len(components)}",
        f"- 状态: {status}",
    ]

    return _cap_result("\n".join(lines), "ha_diagnose")


async def _diagnose_device(client: HAClient, entity_id: str) -> str:
    """单设备诊断"""
    try:
        state = await client.get_state(entity_id)
    except HANotFoundError:
        return f"未找到设备 {entity_id}"

    current_state = state.get("state", "unknown")
    attrs = state.get("attributes", {})
    friendly_name = attrs.get("friendly_name", entity_id)
    last_changed = state.get("last_changed", "")

    lines = [
        f"# 🔍 设备诊断: {entity_id} ({friendly_name})",
        "",
        "## 基本状态",
        f"- 状态: {current_state}",
        f"- 最后变更: {last_changed[:19].replace('T', ' ') if last_changed else '未知'}",
    ]

    # 判断是否有问题
    if current_state in ("unavailable", "unknown"):
        lines.extend([
            "",
            "## 可能原因",
            "1. 设备离线 — 检查设备电源和网络连接",
            "2. 集成异常 — 对应集成可能需要重新认证",
            "3. 网络问题 — HA 与设备之间通信中断",
            "",
            "## 建议操作",
            "1. 检查设备物理电源",
            "2. 在 HA 中重新加载对应集成",
            "3. 如问题持续，尝试重启 HA",
        ])
    else:
        lines.extend([
            "",
            "## 诊断结果",
            f"设备状态正常 ✅",
        ])

    return _cap_result("\n".join(lines), "ha_diagnose")


async def _diagnose_offline_scan(client: HAClient) -> str:
    """扫描离线设备"""
    states = await client.get_states()

    offline = [
        s for s in states
        if s.get("state") in ("unavailable", "unknown")
    ]

    if not offline:
        return "# 离线设备扫描\n\n✅ 所有设备在线，无异常"

    lines = [f"# 离线设备扫描\n\n发现 {len(offline)} 个离线/异常设备：\n"]

    for i, dev in enumerate(offline[:20], 1):
        eid = dev["entity_id"]
        fname = dev.get("attributes", {}).get("friendly_name", eid)
        st = dev.get("state", "unknown")
        lines.append(f"{i}. {eid} ({fname}) — {st}")

    if len(offline) > 20:
        lines.append(f"\n... 及其他 {len(offline) - 20} 个")

    return _cap_result("\n".join(lines), "ha_diagnose")


async def _diagnose_automations(client: HAClient) -> str:
    """检查自动化规则状态"""
    states = await client.get_states(domain="automation")

    if not states:
        return "# 自动化规则检查\n\n未找到自动化规则"

    enabled = [s for s in states if s.get("state") == "on"]
    disabled = [s for s in states if s.get("state") == "off"]

    lines = [
        "# 自动化规则检查",
        f"\n共 {len(states)} 个规则，{len(enabled)} 个启用，{len(disabled)} 个禁用",
        "",
    ]

    if disabled:
        lines.append("## 已禁用的规则")
        for i, rule in enumerate(disabled[:10], 1):
            eid = rule["entity_id"]
            fname = rule.get("attributes", {}).get("friendly_name", eid)
            lines.append(f"{i}. {fname} ({eid})")
        if len(disabled) > 10:
            lines.append(f"... 及其他 {len(disabled) - 10} 个")

    if enabled:
        lines.append("\n## 已启用的规则")
        for i, rule in enumerate(enabled[:10], 1):
            eid = rule["entity_id"]
            fname = rule.get("attributes", {}).get("friendly_name", eid)
            last_triggered = rule.get("attributes", {}).get("last_triggered", "从未")
            if last_triggered and last_triggered != "从未":
                last_triggered = last_triggered[:19].replace("T", " ")
            lines.append(f"{i}. {fname} — 最后触发: {last_triggered}")
        if len(enabled) > 10:
            lines.append(f"... 及其他 {len(enabled) - 10} 个")

    return _cap_result("\n".join(lines), "ha_diagnose")


async def _diagnose_error_log(client: HAClient) -> str:
    """获取错误日志"""
    log_content = await client.get_error_log()

    # 截断到 2000 字符
    max_len = 2000
    if len(log_content) > max_len:
        log_content = log_content[-max_len:]
        log_content = f"[日志已截断，显示最后 {max_len} 字符]\n\n" + log_content

    return _cap_result(f"# Home Assistant 错误日志\n\n```\n{log_content}\n```", "ha_diagnose")


# ============ 工具导出 ============

HA_TOOLS = [ha_query, ha_control, ha_diagnose]
