# .specify/memory 指南

> Speckit 治理文档目录，存放项目宪法等治理文件。

## 文件

| 文件 | 用途 |
|------|------|
| `constitution.md` | 项目宪法 v1.7.0 — 定义不可违背的原则、架构约束、代码质量标准和测试要求。所有由 AI 代理生成的规范、计划和代码必须遵守 |

## 宪法核心内容

- **第一条**：架构原则（分层架构、数据一致性、安全要求）
- **代码风格**：PEP 8 + Black (88字符) / ESLint + Prettier
- **测试要求**：总体 >= 80%、服务层 >= 95%
- **强制参考**：编码时必须参考 `docs/constitution-examples.md` 中的实现模式

## 注意

- 宪法是所有开发行为的最高约束
- 修改宪法使用 `/speckit.constitution` 命令
- 宪法与 `CLAUDE.md`（根目录）同步更新

<claude-mem-context>

</claude-mem-context>