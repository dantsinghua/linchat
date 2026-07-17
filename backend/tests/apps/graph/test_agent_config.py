"""get_agent_config channel 透传单元测试 (wn-linchat-brain C1)

验证 channel 参数一路注入 configurable + metadata.langfuse_tags，
为 C2 出站防串台路由铺路。
"""

from apps.graph.agent import get_agent_config


def test_get_agent_config_default_web():
    """默认 channel=web：configurable.channel + langfuse_tags 均为 web。"""
    config = get_agent_config(7)
    assert config["configurable"]["channel"] == "web"
    assert config["configurable"]["user_id"] == "7"
    assert config["configurable"]["thread_id"] == "user_7"
    assert config["metadata"]["langfuse_tags"] == ["channel:web"]
    assert config["metadata"]["channel"] == "web"


def test_get_agent_config_channel_voice():
    """channel=voice 透传到 configurable + metadata。"""
    config = get_agent_config(7, channel="voice")
    assert config["configurable"]["channel"] == "voice"
    assert config["metadata"]["langfuse_tags"] == ["channel:voice"]
    assert config["metadata"]["channel"] == "voice"


def test_get_agent_config_channel_wechat():
    """channel=wechat 透传（为 C2 老公 channel 铺路）。"""
    config = get_agent_config(7, channel="wechat")
    assert config["configurable"]["channel"] == "wechat"
    assert config["metadata"]["langfuse_tags"] == ["channel:wechat"]
    assert config["metadata"]["channel"] == "wechat"


def test_get_agent_config_callbacks_and_channel():
    """callbacks 与 channel 同时传入互不干扰。"""
    cb = object()
    config = get_agent_config(7, callbacks=[cb], channel="wechat")
    assert config["callbacks"] == [cb]
    assert config["configurable"]["channel"] == "wechat"
