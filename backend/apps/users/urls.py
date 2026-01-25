"""
用户认证模块路由

参考: process-model.md#一、用户登录流程（P_AUTH_001）
"""
from django.urls import path

from apps.users.views import CaptchaView, LoginView, LogoutView, MeView

urlpatterns = [
    # 验证码 - 公开接口
    # GET /api/v1/auth/captcha
    path("captcha", CaptchaView.as_view(), name="captcha"),

    # 登录 - 公开接口
    # POST /api/v1/auth/login
    path("login", LoginView.as_view(), name="login"),

    # 登出 - 需要认证
    # POST /api/v1/auth/logout
    path("logout", LogoutView.as_view(), name="logout"),

    # 当前用户信息 - 需要认证
    # GET /api/v1/auth/me
    path("me", MeView.as_view(), name="me"),
]
