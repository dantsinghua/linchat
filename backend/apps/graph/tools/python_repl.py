"""Python 代码执行工具 — 进程级沙箱

user_id 通过 RunnableConfig 隐式注入 [R-004]。
安全限制：环境变量清空、30秒超时、输出截断 4096 字符。
"""

import asyncio
import logging
import tempfile

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool

logger = logging.getLogger(__name__)

MAX_OUTPUT_LENGTH = 4096
EXEC_TIMEOUT = 30


def _get_user_id(config: RunnableConfig) -> int:
    user_id = config.get("configurable", {}).get("user_id")
    if user_id is None:
        raise ValueError("user_id not found in RunnableConfig")
    return int(user_id)


@tool
async def python_exec(code: str, config: RunnableConfig) -> str:
    """执行 Python 代码并返回结果。用于数学计算、数据处理、验证推理等。使用 print() 输出结果。"""
    user_id = _get_user_id(config)
    logger.info("python_exec called by user_id=%s, code_len=%d", user_id, len(code))

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", delete=True
    ) as f:
        f.write(code)
        f.flush()

        proc = await asyncio.create_subprocess_exec(
            "python3",
            f.name,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={
                "PATH": "/usr/bin:/usr/local/bin",
                "HOME": "/tmp",
                "LANG": "en_US.UTF-8",
            },
        )

        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=EXEC_TIMEOUT
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.communicate()
            return f"执行超时（超过{EXEC_TIMEOUT}秒）"

    output_parts: list[str] = []
    if stdout:
        output_parts.append(stdout.decode("utf-8", errors="replace"))
    if stderr:
        output_parts.append(f"[stderr]\n{stderr.decode('utf-8', errors='replace')}")

    if not output_parts:
        return "代码执行完成，无输出。"

    result = "\n".join(output_parts)
    if len(result) > MAX_OUTPUT_LENGTH:
        result = result[:MAX_OUTPUT_LENGTH] + f"\n...(输出已截断，共{len(result)}字符)"
    return result


REPL_TOOLS = [python_exec]
