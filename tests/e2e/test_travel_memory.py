"""
旅游场景 E2E 测试 — 10 轮连续对话，测试记忆的存储和检索

测试逻辑：
1. 前几轮告诉 AI 旅游偏好和计划（触发 mem_cache）
2. 中间轮问一些需要记忆检索的问题（触发 mem_search）
3. 后几轮修改偏好 + 验证记忆更新（触发 mem_update）

运行: python tests/e2e/test_travel_memory.py
"""

import json
import time
import requests
import redis

# ============ 配置 ============
BASE_URL = "http://localhost:8002/api/v1"
REDIS_PASSWORD = "redis_linchat_123"

# 10 个连续问题 — 旅游场景
QUESTIONS = [
    # === 第 1-3 轮：建立偏好，期望触发 mem_cache ===
    {
        "q": "我计划今年五月去日本旅行14天，预算大概3万人民币，我比较喜欢自然风景和温泉，不太喜欢购物。帮我记住这些偏好。",
        "expect": "记忆保存/确认偏好",
        "memory_action": "cache",
    },
    {
        "q": "补充一下，我有乳糖不耐受，吃不了奶制品。还有我喜欢摄影，会带一台索尼A7M4。这些也帮我记住。",
        "expect": "补充记忆",
        "memory_action": "cache",
    },
    {
        "q": "我之前去过东京和大阪，这次想去没去过的地方。北海道和九州我都感兴趣，帮我记住这个。",
        "expect": "记忆保存",
        "memory_action": "cache",
    },
    
    # === 第 4-6 轮：基于已存记忆做推荐，期望触发 mem_search ===
    {
        "q": "根据我之前告诉你的偏好，帮我推荐一个14天的行程路线。",
        "expect": "基于记忆推荐（应提到自然风景/温泉/北海道九州/预算3万）",
        "memory_action": "search",
    },
    {
        "q": "行程中有没有适合摄影的景点？我带了相机想好好拍。",
        "expect": "应记得用户带了索尼A7M4",
        "memory_action": "search",
    },
    {
        "q": "帮我推荐一些当地美食，但要注意我的饮食限制。",
        "expect": "应记得乳糖不耐受，避免推荐奶制品",
        "memory_action": "search",
    },
    
    # === 第 7-8 轮：修改偏好，期望触发 mem_update ===
    {
        "q": "我改主意了，预算可以提高到5万。另外我现在也想体验一下日本的城市文化和夜生活。帮我更新之前的偏好记录。",
        "expect": "更新记忆（预算3万→5万，新增城市文化兴趣）",
        "memory_action": "update",
    },
    {
        "q": "时间也改一下，从14天延长到21天，六月出发而不是五月。更新一下记录。",
        "expect": "更新记忆（14天→21天，五月→六月）",
        "memory_action": "update",
    },
    
    # === 第 9-10 轮：验证记忆准确性 ===
    {
        "q": "总结一下你记住的我所有的旅行偏好和计划，让我确认一下。",
        "expect": "完整总结：21天/6月/5万预算/自然+城市/温泉/乳糖不耐/A7M4/北海道九州",
        "memory_action": "search",
    },
    {
        "q": "基于更新后的计划，重新帮我规划一个21天的行程。",
        "expect": "基于最新记忆规划（应反映所有更新）",
        "memory_action": "search",
    },
]


def login() -> requests.Session:
    """登录并返回带 cookie 的 session"""
    s = requests.Session()
    
    # 获取 captcha
    cap = s.get(f"{BASE_URL}/auth/captcha").json()["data"]
    captcha_id = cap["captcha_id"]
    
    # 从 Redis 读验证码答案
    r = redis.Redis(host="localhost", port=6379, db=0, password=REDIS_PASSWORD)
    code = r.get(f"auth:captcha:{captcha_id}").decode()
    
    # SM4 加密密码
    import django, os
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core.settings")
    django.setup()
    from apps.users.crypto import sm4_encrypt
    enc_pwd = sm4_encrypt("!9871229Qing")
    
    # 登录
    resp = s.post(f"{BASE_URL}/auth/login", json={
        "username": "admin",
        "password": enc_pwd,
        "captcha_id": captcha_id,
        "captcha_code": code,
    })
    data = resp.json()
    assert data["code"] == "SUCCESS", f"登录失败: {data}"
    print(f"✅ 登录成功: user_id={data['data']['user_id']}")
    return s


def send_chat(session: requests.Session, content: str) -> dict:
    """发送聊天消息，解析 SSE 流式响应"""
    resp = session.post(
        f"{BASE_URL}/chat/",
        json={"content": content},
        headers={"Content-Type": "application/json"},
        stream=True,
    )
    
    if resp.status_code != 200:
        return {"error": f"HTTP {resp.status_code}: {resp.text[:200]}"}
    
    full_content = ""
    message_id = None
    events = []
    
    for line in resp.iter_lines(decode_unicode=True):
        if not line or not line.startswith("data: "):
            continue
        try:
            data = json.loads(line[6:])
            events.append(data.get("type", "unknown"))
            
            if data.get("type") == "content":
                full_content += data.get("content", "")
            elif data.get("type") == "done":
                message_id = data.get("message_id")
            elif data.get("type") == "error":
                return {"error": data.get("content", "未知错误"), "events": events}
        except json.JSONDecodeError:
            continue
    
    return {
        "content": full_content,
        "message_id": message_id,
        "events": events,
        "content_length": len(full_content),
    }


def check_memory_db(user_id: int) -> list:
    """查看数据库中的记忆记录"""
    from apps.memory.models import UserMemory
    memories = UserMemory.objects.filter(user_id=user_id).order_by("-created_at")
    return [
        {
            "id": m.id,
            "content": m.content[:100],
            "status": m.embedding_status,
            "created": str(m.created_at)[:19],
        }
        for m in memories
    ]


def run_test():
    """运行 10 轮对话测试"""
    print("=" * 60)
    print("🗾 旅游场景记忆测试 — 10 轮连续对话")
    print("=" * 60)
    
    session = login()
    results = []
    
    for i, q in enumerate(QUESTIONS, 1):
        print(f"\n{'─' * 60}")
        print(f"📤 第 {i}/10 轮 [{q['memory_action']}]")
        print(f"   问: {q['q'][:60]}...")
        print(f"   期望: {q['expect']}")
        print(f"{'─' * 60}")
        
        start = time.time()
        result = send_chat(session, q["q"])
        elapsed = time.time() - start
        
        if "error" in result:
            print(f"   ❌ 错误: {result['error']}")
            results.append({"round": i, "status": "error", "error": result["error"]})
            continue
        
        # 检查回复中是否包含工具调用的痕迹
        content = result["content"]
        events = result["events"]
        has_memory_event = any("context_compact" in e for e in events)
        
        print(f"   📥 回复 ({result['content_length']} 字, {elapsed:.1f}s):")
        # 打印前 200 字
        preview = content[:200].replace("\n", " ")
        print(f"   {preview}...")
        print(f"   SSE events: {set(events)}")
        
        results.append({
            "round": i,
            "status": "ok",
            "length": result["content_length"],
            "time": round(elapsed, 1),
            "events": list(set(events)),
            "message_id": result["message_id"],
        })
        
        # 每轮之间稍等一下，让 embedding 处理
        if i < len(QUESTIONS):
            time.sleep(2)
    
    # 最终检查记忆数据库
    print(f"\n{'=' * 60}")
    print("📊 测试结果汇总")
    print(f"{'=' * 60}")
    
    ok_count = sum(1 for r in results if r["status"] == "ok")
    err_count = sum(1 for r in results if r["status"] == "error")
    print(f"   ✅ 成功: {ok_count}/10")
    print(f"   ❌ 失败: {err_count}/10")
    
    if ok_count > 0:
        avg_time = sum(r["time"] for r in results if r["status"] == "ok") / ok_count
        print(f"   ⏱️  平均响应: {avg_time:.1f}s")
    
    # 查看记忆记录
    print(f"\n📝 数据库记忆记录:")
    try:
        memories = check_memory_db(user_id=4)
        for m in memories:
            print(f"   [{m['id']}] {m['content']}... (status={m['status']}, {m['created']})")
        print(f"   总计: {len(memories)} 条记忆")
    except Exception as e:
        print(f"   查询失败: {e}")
    
    return results


if __name__ == "__main__":
    import sys, os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
    os.chdir(os.path.join(os.path.dirname(__file__), "..", ".."))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core.settings")
    
    import django
    django.setup()
    
    run_test()
