"""
成员管理路由

GET/POST /api/v1/members/
"""

from django.urls import path

from apps.users.views import MemberListCreateView

urlpatterns = [
    path("", MemberListCreateView.as_view(), name="member-list-create"),
]
