# 007 contracts — Home Assistant REST API 契约

> HAClient 对 Home Assistant REST API 的接口约定，定义 LinChat 如何与 HA 服务通信。

## 文件

| 文件 | 内容 |
|------|------|
| `ha-api-contract.md` | HA REST API 契约 — 认证方式（Bearer Token）、端点定义（GET /api/states/{entity_id}、POST /api/services/{domain}/{service} 等）、错误码映射（401/404/503 等）|

## 说明

本契约定义的是 `HAClient`（`backend/apps/graph/tools/ha_client.py`）调用 Home Assistant 外部服务时的 HTTP 接口规范。SubAgent 工具层面的输入/输出契约见 `specs/006-subagent-tools/contracts/ha-tools-contract.md`。
