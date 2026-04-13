"""桥接服务配置 - 从 .env 文件或环境变量读取。

配置优先级: 环境变量 > .env 文件 > 默认值。
.env 文件路径为本模块同目录下的 `.env`。
"""

import logging
import os
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


def _parse_env_file(env_path: Path) -> dict[str, str]:
    """解析 .env 文件，返回键值对字典。

    支持格式:
      KEY=value
      KEY="value"
      KEY='value'
      # 注释行
      空行
    """
    result: dict[str, str] = {}
    if not env_path.is_file():
        return result

    with open(env_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            # 跳过空行和注释
            if not line or line.startswith("#"):
                continue
            # 按第一个 = 分割
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip()
            # 去除引号
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
                value = value[1:-1]
            result[key] = value

    return result


@dataclass
class BridgeConfig:
    """桥接服务配置。

    Attributes:
        UDP_PORT: UDP 监听端口，接收 ESP32 音频数据包。
        WS_URL: LinChat WebSocket 语音端点 URL。
        DEVICE_TOKEN: 设备认证 Token（必填，无默认值）。
        LOG_LEVEL: 日志级别。
    """

    UDP_PORT: int = 12345
    WS_URL: str = "ws://localhost:8002/ws/voice/"
    DEVICE_TOKEN: str = ""
    LOG_LEVEL: str = "INFO"

    @classmethod
    def load(cls) -> "BridgeConfig":
        """从 .env 文件和环境变量加载配置。

        环境变量优先于 .env 文件中的同名配置。

        Returns:
            BridgeConfig 实例。

        Raises:
            ValueError: DEVICE_TOKEN 未设置时抛出。
        """
        # 读取 .env 文件（与本文件同目录）
        env_path = Path(__file__).parent / ".env"
        file_vars = _parse_env_file(env_path)

        def get(key: str, default: str = "") -> str:
            """优先环境变量，其次 .env 文件，最后默认值。"""
            return os.environ.get(key, file_vars.get(key, default))

        config = cls(
            UDP_PORT=int(get("UDP_PORT", "12345")),
            WS_URL=get("WS_URL", "ws://localhost:8002/ws/voice/"),
            DEVICE_TOKEN=get("DEVICE_TOKEN", ""),
            LOG_LEVEL=get("LOG_LEVEL", "INFO"),
        )

        if not config.DEVICE_TOKEN:
            raise ValueError(
                "DEVICE_TOKEN 未设置。请在 .env 文件或环境变量中配置 DEVICE_TOKEN，"
                "该 Token 用于桥接服务连接 LinChat WebSocket 语音端点的设备认证。"
            )

        return config
