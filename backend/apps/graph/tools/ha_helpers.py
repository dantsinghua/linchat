import fnmatch
import logging
from typing import Any

import redis.asyncio as aioredis
from django.conf import settings

from apps.graph.tools.ha_client import HAClient, HANotFoundError

logger = logging.getLogger(__name__)
_MAX_DISPLAY_ITEMS = 20


def _cap(text: str, tool_name: str) -> str:
    from apps.graph.tools import cap_tool_result
    return cap_tool_result(text, tool_name)
RATE_LIMITS = {"control": 10, "query": 30, "diagnose": 5}
ACTION_DESC: dict[str, str] = {
    "turn_on": "开启", "turn_off": "关闭", "toggle": "切换", "set_brightness": "调节亮度",
    "set_color": "设置颜色", "set_color_temp": "设置色温", "set_temperature": "设置温度",
    "set_hvac_mode": "设置模式", "set_fan_speed": "设置风速", "play": "播放", "pause": "暂停",
    "volume": "调节音量", "scene": "激活场景", "script": "执行脚本", "lock": "锁定",
    "unlock": "解锁", "open_cover": "打开", "close_cover": "关闭"}
_SENS = [("unlock", "*", "敏感", "解锁"), ("open_cover", "cover.garage_*", "敏感", "打开车库门"),
         ("turn_off", "automation.*", "危险", "禁用自动化规则")]
_ATTR_FMT = [("brightness", lambda v: f"亮度: {v}/255 ({round(v/255*100)}%)" if v is not None else "亮度: N/A"),
    ("color_temp_kelvin", lambda v: f"色温: {v}K" if v is not None else "色温: N/A"),
    ("temperature", lambda v: f"温度: {v}°C" if v is not None else "温度: N/A"),
    ("current_temperature", lambda v: f"当前温度: {v}°C" if v is not None else "当前温度: N/A"),
    ("hvac_mode", lambda v: f"模式: {v}" if v is not None else "模式: N/A"),
    ("volume_level", lambda v: f"音量: {int(v*100)}%" if v is not None else "音量: N/A")]

def _get_user_id(config: Any) -> int:
    from apps.graph.tools.user_id import get_user_id
    if config is None: raise ValueError("config is required for HA tools")
    return get_user_id(config)
async def _check_rate_limit(user_id: int, tool_type: str) -> str | None:
    limit, key = RATE_LIMITS.get(tool_type, 10), f"ha:{tool_type}:rate:{user_id}"
    r: aioredis.Redis = aioredis.from_url(settings.REDIS_URL)
    try:
        count = await r.incr(key)
        if count == 1: await r.expire(key, 60)
        return f"操作过于频繁，请稍后再试（{tool_type} 限制 {limit} 次/分钟）" if count > limit else None
    finally:
        await r.aclose()
def _is_blocked(entity_id: str) -> bool:
    return entity_id in getattr(settings, "HA_BLOCKED_ENTITIES", [])
def _is_sensitive(action: str, entity_id: str) -> tuple[bool, str | None]:
    for act, pat, lvl, desc in _SENS:
        if action == act and fnmatch.fnmatch(entity_id, pat):
            body = "禁用自动化可能影响系统正常运行，请" if lvl == "危险" else "这是一个涉及安全的操作，请"
            return (True, f"⚠️ {lvl}操作确认\n即将执行: {desc} {entity_id}\n{body}确认是否继续。\n"
                    f'回复"确认{desc[:2]}"以执行，或"取消"以放弃。')
    return (False, None)
def _format_state(state: dict) -> str:
    eid, attrs = state.get("entity_id", "unknown"), state.get("attributes", {})
    fname, st, lc = attrs.get("friendly_name", eid), state.get("state", "unknown"), state.get("last_changed", "")
    lines = [f"# 设备状态: {eid}", f"- 名称: {fname}", f"- 状态: {st}"]
    for key, fmt in _ATTR_FMT:
        if key in attrs: lines.append(f"- {fmt(attrs[key])}")
    if lc: lines.append(f"- 最后变更: {lc[:19].replace('T', ' ')}")
    return _cap("\n".join(lines), "ha_query")
def _format_device_list(states: list[dict], domain: str | None) -> str:
    if not states: return "未找到设备" + (f"（域: {domain}）" if domain else "")
    grouped: dict[str, list[dict]] = {}
    for s in states: grouped.setdefault(s["entity_id"].split(".")[0], []).append(s)
    lines = ["# 设备列表" + (f" (域: {domain})" if domain else "")]
    for d, devs in sorted(grouped.items()):
        lines.append(f"\n## {d} ({len(devs)} 个)")
        for i, dev in enumerate(devs[:_MAX_DISPLAY_ITEMS], 1):
            eid, a, st = dev["entity_id"], dev.get("attributes", {}), dev.get("state", "unknown")
            b = a.get("brightness")
            si = f"{st} ({round(b/255*100)}%)" if b is not None else st
            lines.append(f"{i}. {eid} — {a.get('friendly_name', eid)} — {si}")
        if len(devs) > _MAX_DISPLAY_ITEMS: lines.append(f"... 及其他 {len(devs) - _MAX_DISPLAY_ITEMS} 个")
    on_n = sum(1 for s in states if s.get("state") in ("on", "playing", "open"))
    lines.append(f"\n共 {len(states)} 个设备，{on_n} 个开启/活跃")
    return _cap("\n".join(lines), "ha_query")
def _format_history(history: list[list[dict]], entity_id: str, hours: int) -> str:
    if not history or not history[0]: return f"设备 {entity_id} 在过去 {hours} 小时内无状态变化记录"
    changes = history[0]
    lines = [f"# 历史记录: {entity_id} (过去 {hours} 小时)"]
    for i, c in enumerate(changes[-_MAX_DISPLAY_ITEMS:], 1):
        lc = c.get("last_changed", "")
        lines.append(f"{i}. {lc[:19].replace('T', ' ') if lc else ''} → {c.get('state', 'unknown')}")
    if len(changes) > _MAX_DISPLAY_ITEMS: lines.append(f"... 共 {len(changes)} 条记录")
    return _cap("\n".join(lines), "ha_query")

def _format_control_result(action: str, entity_id: str, result: list | None) -> str:
    desc = ACTION_DESC.get(action, action)
    if not result or len(result) == 0:
        return (f"⚠️ 操作未生效: {desc} {entity_id}\n"
                f"Home Assistant 未返回设备状态，该 entity_id 可能不存在。\n"
                f"请使用 ha_query(query_type=\"list\") 查看正确的 entity_id。")
    a, st = result[0].get("attributes", {}), result[0].get("state", "unknown")
    fname, detail = a.get("friendly_name", entity_id), f"当前状态: {st}"
    b = a.get("brightness")
    if b is not None:
        detail += f", 亮度 {b}/255 ({round(b/255*100)}%)"
    t = a.get("temperature")
    if t is not None:
        detail += f", 温度 {t}°C"
    return f"✅ 已执行: {desc} {fname} ({entity_id})\n{detail}"

async def _diagnose_health(client: HAClient) -> str:
    healthy, cfg = await client.check_health(), await client.get_config()
    ver, comps = cfg.get("version", "unknown"), cfg.get("components", [])
    return _cap("\n".join(["# 🏠 Home Assistant 系统状态", f"- 版本: {ver}",
        f"- 组件数: {len(comps)}", f"- 状态: {'正常 ✅' if healthy else '异常 ⚠️'}"]), "ha_diagnose")

async def _diagnose_device(client: HAClient, entity_id: str) -> str:
    try:
        state = await client.get_state(entity_id)
    except HANotFoundError:
        return f"未找到设备 {entity_id}"
    st, a = state.get("state", "unknown"), state.get("attributes", {})
    fname, lc = a.get("friendly_name", entity_id), state.get("last_changed", "")
    lines = [f"# 🔍 设备诊断: {entity_id} ({fname})", "", "## 基本状态",
             f"- 状态: {st}", f"- 最后变更: {lc[:19].replace('T', ' ') if lc else '未知'}"]
    if st in ("unavailable", "unknown"):
        lines += ["", "## 可能原因", "1. 设备离线 — 检查设备电源和网络连接",
            "2. 集成异常 — 对应集成可能需要重新认证", "3. 网络问题 — HA 与设备之间通信中断",
            "", "## 建议操作", "1. 检查设备物理电源", "2. 在 HA 中重新加载对应集成", "3. 如问题持续，尝试重启 HA"]
    else:
        lines += ["", "## 诊断结果", "设备状态正常 ✅"]
    return _cap("\n".join(lines), "ha_diagnose")

async def _diagnose_offline_scan(client: HAClient) -> str:
    states = await client.get_states()
    offline = [s for s in states if s.get("state") in ("unavailable", "unknown")]
    if not offline: return "# 离线设备扫描\n\n✅ 所有设备在线，无异常"
    lines = [f"# 离线设备扫描\n\n发现 {len(offline)} 个离线/异常设备：\n"]
    for i, d in enumerate(offline[:_MAX_DISPLAY_ITEMS], 1):
        eid = d["entity_id"]
        lines.append(f"{i}. {eid} ({d.get('attributes', {}).get('friendly_name', eid)}) — {d.get('state', 'unknown')}")
    if len(offline) > _MAX_DISPLAY_ITEMS: lines.append(f"\n... 及其他 {len(offline) - _MAX_DISPLAY_ITEMS} 个")
    return _cap("\n".join(lines), "ha_diagnose")

def _fmt_rules(rules: list[dict], n: int, trigger: bool = False) -> list[str]:
    lines = []
    for i, r in enumerate(rules[:n], 1):
        eid, fn = r["entity_id"], r.get("attributes", {}).get("friendly_name", r["entity_id"])
        if trigger:
            lt = r.get("attributes", {}).get("last_triggered", "从未")
            if lt and lt != "从未": lt = lt[:19].replace("T", " ")
            lines.append(f"{i}. {fn} — 最后触发: {lt}")
        else: lines.append(f"{i}. {fn} ({eid})")
    if len(rules) > n: lines.append(f"... 及其他 {len(rules) - n} 个")
    return lines

async def _diagnose_automations(client: HAClient) -> str:
    states = await client.get_states(domain="automation")
    if not states: return "# 自动化规则检查\n\n未找到自动化规则"
    enabled = [s for s in states if s.get("state") == "on"]
    disabled = [s for s in states if s.get("state") == "off"]
    lines = ["# 自动化规则检查", f"\n共 {len(states)} 个规则，{len(enabled)} 个启用，{len(disabled)} 个禁用", ""]
    if disabled: lines += ["## 已禁用的规则"] + _fmt_rules(disabled, 10)
    if enabled: lines += ["\n## 已启用的规则"] + _fmt_rules(enabled, 10, trigger=True)
    return _cap("\n".join(lines), "ha_diagnose")

async def _diagnose_error_log(client: HAClient) -> str:
    log = await client.get_error_log()
    if len(log) > 2000: log = f"[日志已截断，显示最后 2000 字符]\n\n" + log[-2000:]
    return _cap(f"# Home Assistant 错误日志\n\n```\n{log}\n```", "ha_diagnose")
