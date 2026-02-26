# 006 contracts — SubAgent 工具 API 契约

> SubAgent 工具的内部 API 契约文档。这些工具通过 LangGraph Agent 内部调用，不暴露为 HTTP 接口。

## 文件

| 文件 | 内容 |
|------|------|
| `ha-tools-contract.md` | Home Assistant 工具输入/输出契约，定义 3 个工具函数签名：`ha_query`（设备状态查询）、`ha_control`（设备控制）、`ha_diagnose`（设备诊断），供 Agent 和测试使用 |

## 说明

本目录的契约文件定义的是 SubAgent 内部工具的调用签名和返回格式，而非 REST API。HA SubAgent 内部通过 `HAClient` 调用 Home Assistant REST API，具体的 HA REST API 契约见 `specs/007-home-assistant-tools/contracts/ha-api-contract.md`。
