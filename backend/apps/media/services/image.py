import logging
from io import BytesIO

from PIL import Image

logger = logging.getLogger(__name__)


def get_image_dimensions(file_bytes: bytes) -> tuple[int, int]:
    try:
        with Image.open(BytesIO(file_bytes)) as img:
            return img.width, img.height
    except Exception as e:
        logger.warning(f"获取图片尺寸失败: {e}")
        return 0, 0
