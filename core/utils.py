import re


def replace_placeholders(message: str, params: dict) -> str:
    def replacer(match):
        key = match.group(1)
        return str(params.get(key, f"{{{key}}}"))

    return re.sub(r"\{(\w+)\}", replacer, message)


# Basic message mapping (ported from message.yaml context)
MESSAGES = {
    "common.need_api_key": "未配置 api_key，部分功能将不可用。请前往 https://end.shallow.ink 获取秘钥并填写。",
    "enduid.login_ok": "绑定成功！\n角色：{nickname} (UID: {role_id}, 服: {server_id})\n当前共有 {count} 个账号。",
    "enduid.auth_link_intro": "请在网页中完成授权：",
    "enduid.auth_link_expiry": "链接有效期至：{time}",
    "enduid.auth_link_wait": "授权完成后，请耐心等待机器人确认...",
    "enduid.auth_timeout": "本次授权已超时，请重新输入指令进行授权。",
    "enduid.auth_rejected": "授权已被拒绝。",
    "enduid.auth_expired": "授权链接已过期。",
    "enduid.bind_help": (
        "【终末地绑定帮助】\n"
        "1. 授权登陆：输入 :授权登陆，点击生成的链接在网页完成授权。\n"
        "2. 扫码绑定：输入 :扫码绑定，使用森空岛 App 扫码。\n"
        "3. 手机绑定：输入 :手机绑定 [手机号]，仅限私聊。\n"
        "4. 绑定列表：查看已绑定账号和序号。\n"
        "5. 切换绑定 [序号]：切换当前使用的账号。\n"
        "6. 删除绑定 [序号]：删除指定账号。"
    ),
    "stamina.loading": "正在查询理智数据，请稍候...",
    "common.get_role_failed": "获取角色数据失败，请检查绑定状态或 API 连通性。",
    "common.query_failed": "查询失败：{error}",
    "stamina.subscribe_ok_threshold": "订阅成功！当理智达到 {threshold} 时将推送提醒。",
    "stamina.subscribe_ok_full": "订阅成功！当理智回满时将推送提醒。",
}


def get_message(path: str, params: dict = None) -> str:
    msg = MESSAGES.get(path, f"[消息未配置: {path}]")
    if params:
        return replace_placeholders(msg, params)
    return msg
