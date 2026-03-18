"""用户模块自定义异常"""

from apps.common.exceptions import BusinessException


class UsernameExistsError(BusinessException):
    default_message = "用户名已存在"
    error_code = "USERNAME_EXISTS"


class VoiceprintRegistrationError(BusinessException):
    default_message = "声纹注册失败"
    error_code = "VOICEPRINT_FAILED"
