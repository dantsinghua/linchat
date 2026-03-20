def get_user_id(config) -> int:
    uid = (config or {}).get("configurable", {}).get("user_id")
    if uid is None:
        raise ValueError("user_id not found in RunnableConfig")
    return int(uid)
