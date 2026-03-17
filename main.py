import asyncio
import datetime
import hashlib
import ipaddress
import mimetypes
import os
import random
import re
import socket
import time
import uuid
import base64
import tempfile
import httpx
from urllib.parse import urlparse
from astrbot.api import logger
from astrbot.api.event import filter, AstrMessageEvent, MessageChain
from astrbot.api.star import Context, Star, register, StarTools
from astrbot.core import AstrBotConfig
from astrbot.core.message.components import Plain, Image, At, Node, Nodes

from .core.client import EndfieldClient
from .core.user import (
    UserManager,
    SimulateManager,
    AnnouncementManager,
    MaaendManager,
    SanityManager,
    SignManager,
    TicketManager,
)
from .core.utils import get_message
from .core.render import Renderer


def get_cover_url(item: dict) -> str:
    if not item:
        return ""
    imgs = item.get("images", [])
    if not imgs:
        return ""
    first = imgs[0]
    if isinstance(first, str):
        return first
    if isinstance(first, dict):
        if first.get("url"):
            return first["url"]
        dis = first.get("display_infos") or first.get("displayInfos")
        if dis and isinstance(dis, list) and len(dis) > 0:
            return dis[0].get("url", "")
    return ""


def format_publish_time(ts) -> str:
    if not ts:
        return ""
    try:
        import datetime

        d = datetime.datetime.fromtimestamp(int(ts))
        return d.strftime("%m/%d %H:%M")
    except Exception:
        return ""


def get_content_text(data: dict) -> str:
    if not data:
        return ""
    texts = data.get("texts")
    if isinstance(texts, list) and texts:
        return "\n".join(
            [
                str(t.get("content", ""))
                for t in texts
                if isinstance(t, dict) and t.get("content")
            ]
        )

    content = data.get("content")
    if isinstance(content, dict):
        blocks = content.get("blocks")
        if isinstance(blocks, list):
            res = []
            for b in blocks:
                if isinstance(b, dict) and b.get("kind") == "text" and "text" in b:
                    txt_prop = b["text"]
                    if isinstance(txt_prop, str):
                        res.append(txt_prop)
                    elif isinstance(txt_prop, dict) and txt_prop.get("text"):
                        res.append(txt_prop["text"])
            return "\n".join(res)
    return ""


def content_to_detail_html(text: str) -> str:
    if not text:
        return ""
    text = str(text)
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def build_caption_content(item: dict) -> str:
    if not item:
        return ""
    texts = item.get("texts")
    if not isinstance(texts, list) or not texts:
        return ""
    parts = []
    for text in texts:
        if isinstance(text, dict) and text.get("content"):
            parts.append(
                f'<div class="detail-text-block">{content_to_detail_html(text["content"])}</div>'
            )
    return "".join(parts)


def build_detail_render_data(item: dict) -> dict:
    cover_url = get_cover_url(item)
    title = item.get("title") or "（未知标题）"
    time_str = format_publish_time(item.get("published_at_ts"))
    time_label = "发布时间"
    content_html = content_to_detail_html(get_content_text(item)) or "（暂无正文）"
    caption_html = build_caption_content(item) or content_html
    return {
        "title": title,
        "timeStr": time_str,
        "timeLabel": time_label,
        "coverUrl": cover_url,
        "contentHtml": content_html,
        "captionHtml": caption_html,
        "copyright": "由 AstrBot & Endfield Plugin 渲染",
        "pageWidth": 720,
    }


@register(
    "astrbot_plugin_endfield",
    "bvzrays & 熵增项目组",
    "终末地协议终端",
    "2.5.0",
    "https://github.com/Entropy-Increase-Team/astrbot_plugin_endfield",
)
class EndfieldPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig = None):
        super().__init__(context)
        self.config = config or {}
        self.api_key = config.get("api_key", "") if config else ""
        self.verify_ssl = config.get("verify_ssl", True) if config else True
        self.auto_sign_in = config.get("auto_sign_in", True) if config else True
        self.auto_sign_in_time = (
            config.get("auto_sign_in_time", "00:05") if config else "00:05"
        )
        self.auto_sign_in_interval = (
            config.get("auto_sign_in_interval", 3) if config else 3
        )
        self.auto_sign_in_notify_group = (
            config.get("auto_sign_in_notify_group", "") if config else ""
        )
        self.client = EndfieldClient(self.api_key, verify_ssl=self.verify_ssl)

        # Use StarTools.get_data_dir() for persistence compliance
        data_dir = str(StarTools.get_data_dir())
        if not os.path.exists(data_dir):
            os.makedirs(data_dir, exist_ok=True)

        self.user_mgr = UserManager(data_dir)
        self.sim_mgr = SimulateManager(data_dir)
        self.announce_mgr = AnnouncementManager(data_dir)
        self.sanity_mgr = SanityManager(data_dir)
        self.ticket_mgr = TicketManager(data_dir)
        self.maa_mgr = MaaendManager(data_dir)
        self.sign_mgr = SignManager(data_dir)

        res_path = os.path.join(os.path.dirname(__file__), "resources")
        self.renderer = Renderer(res_path, self)
        self._announcement_task_handle = None
        self._sanity_task_handle = None
        self._ticket_task_handle = None
        self._auto_sign_in_task_handle = None
        self._http_client = None
        self.banner_cache = {}

    def _get_server_name(self, acc: dict) -> str:
        """从账号对象中获取可读的服务器名称。"""
        detailed = acc.get("server_name")  # e.g. "China", "Global"
        channel = acc.get("channel_name")  # e.g. "official", "官服"
        login_type = acc.get("login_type")

        if login_type == "skport":
            # 国际服：优先级 1. 地区名 (Global/China/US) 2. 渠道名 (非通用名时) 3. 默认标识
            if detailed:
                return detailed
            if channel and str(channel).lower() not in ["official", "skport"]:
                return channel
            return "国际服"

        # 国服逻辑：保持 官服/B服 显示，符合用户对国服习惯的偏好
        label = "官服" if str(acc.get("server_id", "1")) == "1" else "B服"
        if channel:
            mapping = {"official": "官服", "bilibili": "B服"}
            label = mapping.get(
                str(channel).lower(), label if channel in ["官服", "B服"] else channel
            )

        return label

    def _medal_sort_key(self, medal: dict):
        """
        估算成就稀有度 / 难度，用于排序：
        - 可认证(canCertify) 的优先
        - 初始等级(initLevel/level) 越高优先
        - 类型权重：特殊 > 章节 > 成长 > 其他
        - 最后按获得时间(obtainTs) 倒序
        """
        data = medal.get("achievementData", {}) or {}
        cate = str(data.get("cate") or "")
        init_level = int(data.get("initLevel") or 1)
        level = int(medal.get("level") or init_level or 1)
        can_certify = bool(data.get("canCertify"))
        # 类型大致权重：特殊 > 剧情/章节 > 成长
        cate_weight_map = {
            "achv_type_special": 3,
            "achv_type_quest": 2,
            "achv_type_growth": 1,
        }
        # 未知 cate 给中等权重，避免未收录的稀有类型被排到最后
        cate_score = cate_weight_map.get(cate, 2)
        try:
            ts = int(medal.get("obtainTs") or 0)
        except Exception:
            ts = 0
        # canCertify 单独提权；initLevel 越高越靠前（等级高=更难/更稀有）
        certify_score = 1 if can_certify else 0
        init_lev = int(data.get("initLevel") or 1)
        return (certify_score, cate_score, init_lev, int(level), ts)

    async def get_activity_banner(self, act: dict) -> str:
        name = act.get("name", "")
        if name in self.banner_cache:
            return self.banner_cache[name]

        # Limit cache size to prevent memory leak
        if len(self.banner_cache) > 200:
            self.banner_cache.clear()  # Simple clear if too big

        pc_link = act.get("pc_link", "")
        match = re.search(r"gameEntryId=(\d+)", pc_link)
        if match:
            entry_id = match.group(1)
            try:
                res = await self.client._get(f"/api/wiki/items/{entry_id}")
                if (
                    res
                    and isinstance(res, dict)
                    and "content" in res
                    and "document_map" in res["content"]
                ):
                    for doc in res["content"]["document_map"].values():
                        if "block_map" in doc:
                            for block in doc["block_map"].values():
                                if block.get("kind") == "image" and "image" in block:
                                    img_url = block["image"].get("url", "")
                                    if img_url:
                                        self.banner_cache[name] = img_url
                                        return img_url
            except Exception as e:
                logger.error(f"Failed to fetch wiki banner for {name}: {e}")

        # Fallback to pic
        pic = act.get("pic", "")
        self.banner_cache[name] = pic
        return pic

    async def get_b64(self, rp):
        if not rp:
            return ""

        if rp.startswith("//"):
            rp = "https:" + rp

        cache_dir = os.path.join(self.renderer.res_path, "cache")
        if not os.path.exists(cache_dir):
            os.makedirs(cache_dir, exist_ok=True)

        if rp.startswith("http://") or rp.startswith("https://"):
            url_hash = hashlib.md5(rp.encode()).hexdigest()
            ext = ".png"
            if "." in rp.split("/")[-1]:
                ext = "." + rp.split("/")[-1].split(".")[-1].split("?")[0]
                if len(ext) > 5:
                    ext = ".png"

            # Async SSRF prevention - non-blocking DNS resolution
            try:
                parsed_url = urlparse(rp)
                hostname = parsed_url.hostname
                if not hostname:
                    return ""
                loop = asyncio.get_event_loop()
                addr_info = await loop.getaddrinfo(hostname, None)
                for addr in addr_info:
                    ip = addr[4][0]
                    ip_obj = ipaddress.ip_address(ip)
                    if (
                        ip_obj.is_private
                        or ip_obj.is_loopback
                        or ip_obj.is_unspecified
                        or ip_obj.is_link_local
                        or ip_obj.is_multicast
                    ):
                        logger.warning(
                            f"Blocked potential SSRF access to {rp} (Resolved IP: {ip})"
                        )
                        return ""
            except Exception as e:
                logger.warning(
                    f"Blocked potential SSRF access to {rp} due to resolving error: {e}"
                )
                return ""

            cache_file = os.path.join(cache_dir, f"{url_hash}{ext}")
            if os.path.exists(cache_file):
                return "file:///" + os.path.abspath(cache_file).replace("\\", "/")

            try:
                if self._http_client is None or self._http_client.is_closed:
                    self._http_client = httpx.AsyncClient(verify=self.verify_ssl)
                client = self._http_client
                for attempt in range(3):
                    try:
                        resp = await client.get(rp, timeout=10)
                        if resp.status_code == 200:
                            with open(cache_file, "wb") as f:
                                f.write(resp.content)
                            return "file:///" + os.path.abspath(cache_file).replace(
                                "\\", "/"
                            )
                        break
                    except Exception:
                        if attempt == 2:
                            raise
                        await asyncio.sleep(0.5)
            except Exception as e:
                logger.error(f"Failed to fetch external image {rp}: {e}")
            return rp

        fp = os.path.join(self.renderer.res_path, rp)
        if os.path.exists(fp):
            return "file:///" + os.path.abspath(fp).replace("\\", "/")
        return rp

    async def parallel_download_b64(self, urls):
        """并行下载多个URL并返回本地路径或B64"""
        if not urls:
            return []
        sem = asyncio.Semaphore(10)

        async def _download(u):
            async with sem:
                return await self.get_b64(u)

        tasks = [_download(url) for url in urls]
        return await asyncio.gather(*tasks)

    async def initialize(self):
        # Tasks
        self._announcement_task_handle = asyncio.create_task(self.announcement_task())
        self._sanity_task_handle = asyncio.create_task(self.sanity_task())
        self._ticket_task_handle = asyncio.create_task(self.ticket_task())

        # Auto Sign-in Task
        if self.auto_sign_in:
            self._auto_sign_in_task_handle = asyncio.create_task(
                self.auto_sign_in_task()
            )
            # Startup check: if not signed in today, run it now
            now_date = datetime.datetime.now().strftime("%Y-%m-%d")
            last_date = await self.sign_mgr.get_last_sign_date()
            if now_date != last_date:
                logger.info(
                    f"[Endfield Auto Sign-In] Startup check: Not signed in today ({now_date} != {last_date}). Running now."
                )
                asyncio.create_task(self.run_batch_sign_in())

    @filter.command("zmd")
    async def zmd_help(self, event: AstrMessageEvent):
        """显示终末地插件帮助菜单"""
        render_data = {
            "helpCfg": {
                "title": "终末地协议终端",
                "subTitle": "Endfield Protocol Terminal",
            },
            "helpGroup": [
                {
                    "type": "tips",
                    "tipItems": [{"title": "提示", "text": "指令触发符：/  ·  发送 zmd 查看本页"}],
                },
                {
                    "group": "账号绑定",
                    "list": [
                        {"title": "授权登陆", "desc": "网页安全授权登录（推荐）", "icon": True},
                        {"title": "扫码绑定", "desc": "扫描二维码快捷登录", "icon": True},
                        {"title": "手机绑定 [手机号]", "desc": "验证码登录（暂不可用）", "icon": True},
                        {"title": "国际服登录", "desc": "邮箱+密码登录国际服", "icon": True},
                        {"title": "绑定列表", "desc": "查看所有绑定账号", "icon": True},
                        {"title": "切换绑定 [序号]", "desc": "切换当前主账号", "icon": True},
                        {"title": "删除绑定 [序号]", "desc": "解绑指定账号", "icon": True},
                    ],
                },
                {
                    "group": "数据查询",
                    "list": [
                        {"title": "便签", "desc": "账号数据总览", "icon": True},
                        {"title": "理智", "desc": "理智查询", "icon": True},
                        {"title": "干员列表", "desc": "持有干员图鉴", "icon": True},
                        {"title": "<干员名>面板", "desc": "单干员详情（技能/武器/基质）", "icon": True},
                        {"title": "同步面板", "desc": "同步干员战斗属性数据", "icon": True},
                        {"title": "抽卡记录", "desc": "近期抽卡历史", "icon": True},
                        {"title": "抽卡分析", "desc": "全卡池统计分析", "icon": True},
                        {"title": "抽卡分析同步", "desc": "同步抽卡数据后再分析", "icon": True},
                        {"title": "全服统计", "desc": "全服抽卡统计与排行", "icon": True},
                        {"title": "签到", "desc": "执行每日签到", "icon": True},
                        {"title": "日历", "desc": "活动版本日历", "icon": True},
                        {"title": "帝江号建设", "desc": "帝江号基建进度", "icon": True},
                        {"title": "地区建设", "desc": "地区开发进度与调度券", "icon": True},
                        {"title": "成就列表", "desc": "成就达成情况", "icon": True},
                    ],
                },
                {
                    "group": "公告与订阅",
                    "list": [
                        {"title": "公告", "desc": "官方公告列表", "icon": True},
                        {"title": "公告最新", "desc": "获取最新一条公告全文", "icon": True},
                        {"title": "订阅公告", "desc": "订阅公告推送", "icon": True},
                        {"title": "取消订阅公告", "desc": "取消公告订阅", "icon": True},
                        {"title": "订阅理智", "desc": "理智满值时推送", "icon": True},
                        {"title": "取消订阅理智", "desc": "取消理智订阅", "icon": True},
                        {"title": "订阅调度券", "desc": "调度券满值时推送", "icon": True},
                        {"title": "取消订阅调度券", "desc": "取消调度券订阅", "icon": True},
                    ],
                },
            ],
            "contentWidth": 1280,
            "colCount": 3,
            "colWidth": 380,
            "widthGap": 24,
            "copyright": "Endfield Protocol Terminal | v2.5.0",
            "pluResPath": "file:///"
            + os.path.abspath(self.renderer.res_path).replace("\\", "/")
            + "/",
        }

        try:
            img_url = await self.renderer.render_html("help/help.html", render_data)
            if img_url:
                yield event.image_result(img_url)
                return
        except Exception as e:
            logger.warning(f"渲染菜单失败: {e}")

        # Fallback to plain text if rendering fails
        help_text = "【终末地协议终端 v2.5.0】\n"
        for group in render_data["helpGroup"]:
            if group.get("group"):
                help_text += f"\n{group['group']}\n"
            if group.get("list"):
                for item in group["list"]:
                    help_text += f" {item['title']} - {item['desc']}\n"
        yield event.plain_result(help_text)

    @filter.command("绑定列表")
    async def bind_list(self, event: AstrMessageEvent):
        """查看已绑定账号"""
        user_id = event.get_sender_id()
        bindings = await self.user_mgr.get_user_bindings(user_id)
        if not bindings:
            yield event.plain_result("暂无绑定账号。")
            return

        # Try to render image
        items = []
        for i, b in enumerate(bindings):
            items.append(
                {
                    "index": i + 1,
                    "nickname": b.get("nickname", "未知"),
                    "role_id": b.get("role_id", "未知"),
                    "server_label": self._get_server_name(b),
                    "type_label": b.get("login_type", "未知"),
                    "isPrimary": b.get("is_primary", False),
                    "created_at": time.strftime(
                        "%Y-%m-%d %H:%M", time.localtime(b.get("bind_time", 0) / 1000)
                    )
                    if b.get("bind_time")
                    else "未知",
                }
            )

        render_data = {
            "title": "终末地绑定列表",
            "subtitle": f"共 {len(bindings)} 个绑定",
            "bindings": items,
            "copyright": "Endfield Plugin | AstrBot",
        }

        try:
            img_url = await self.renderer.render_html(
                "enduid/bind-list.html", render_data
            )
            if img_url:
                yield event.image_result(img_url)
                return
        except Exception as e:
            logger.warning(f"渲染绑定列表失败，使用文本回退: {e}")
        # Fallback to text
        msg = "【终末地绑定列表】\n"
        for it in items:
            mark = " ⭐" if it["isPrimary"] else ""
            msg += f"[{it['index']}] {it['nickname']}{mark}\n  UID: {it['role_id']}\n"
        yield event.plain_result(msg)

    @filter.command("切换绑定")
    async def switch_bind(self, event: AstrMessageEvent, index: int):
        """切换当前使用的账号"""
        user_id = event.get_sender_id()
        bindings = await self.user_mgr.get_user_bindings(user_id)
        if not (1 <= index <= len(bindings)):
            yield event.plain_result(
                f"序号错误，请选择 1 到 {len(bindings)} 之间的数字。"
            )
            return

        for i, b in enumerate(bindings):
            b["is_primary"] = i + 1 == index

        await self.user_mgr.save_user_bindings(user_id, bindings)
        yield event.plain_result(f"已切换至账号：{bindings[index - 1]['nickname']}")
        async for res in self.bind_list(event):
            yield res

    @filter.command("删除绑定")
    async def delete_bind(self, event: AstrMessageEvent, index: int):
        """删除指定绑定账号"""
        user_id = event.get_sender_id()
        bindings = await self.user_mgr.get_user_bindings(user_id)
        if not (1 <= index <= len(bindings)):
            yield event.plain_result(
                f"序号错误，请选择 1 到 {len(bindings)} 之间的数字。"
            )
            return

        target = bindings[index - 1]
        binding_id = target.get("binding_id")
        if not binding_id:
            yield event.plain_result("该绑定数据异常无 ID，无法删除。")
            return

        confirm = await self.client.delete_binding(binding_id, user_id)

        await self.user_mgr.delete_user_binding(user_id, binding_id)

        msg = f"已删除绑定：{target['nickname']}"
        if not confirm:
            msg += "\n(后端同步可能稍有延迟，请前往官网检查确认)"
        yield event.plain_result(msg)
        async for res in self.bind_list(event):
            yield res

    async def _send_and_get_msg_id(self, event: AstrMessageEvent, obmsg: list):
        """通过协议端直接发送消息并返回 (client, message_id)"""
        try:
            if event.get_platform_name() == "aiocqhttp":
                from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import (
                    AiocqhttpMessageEvent,
                )

                if isinstance(event, AiocqhttpMessageEvent):
                    client = event.bot
                    group_id = event.get_group_id()
                    if group_id:
                        send_result = await client.send_group_msg(
                            group_id=int(group_id), message=obmsg
                        )
                    else:
                        send_result = await client.send_private_msg(
                            user_id=int(event.get_sender_id()), message=obmsg
                        )
                    if send_result:
                        msg_id = int(send_result.get("message_id"))
                        logger.info(f"[消息追踪] 消息已发送，message_id={msg_id}")
                        return client, msg_id
        except Exception as e:
            logger.warning(f"[消息追踪] 协议端发送失败: {e}")
        return None, None

    def _schedule_recall(self, client, message_id: int, delay: float):
        """使用 asyncio.create_task 调度延迟撤回（和 recall 插件完全一样的模式）"""

        async def _do_recall():
            await asyncio.sleep(delay)
            try:
                await client.delete_msg(message_id=message_id)
                logger.info(f"[撤回] 已撤回消息 {message_id}")
            except Exception as e:
                logger.warning(f"[撤回] 撤回消息失败: {e}")

        return asyncio.create_task(_do_recall())

    @filter.command("授权登陆")
    async def auth_login(self, event: AstrMessageEvent):
        """网页授权登录"""
        self.client.set_caller(
            bot_qq=event.get_self_id(), user_qq=event.get_sender_id()
        )
        if not self.config.get("api_key"):
            yield event.plain_result(get_message("common.need_api_key"))
            return

        user_id = event.get_sender_id()
        client_name = self.config.get("auth_client_name", "终末地机器人")

        auth_req = await self.client.create_authorization_request(
            client_id=user_id, client_name=client_name
        )

        if not auth_req or "request_id" not in auth_req or "auth_url" not in auth_req:
            yield event.plain_result("创建授权请求失败，请检查网络或 API Key。")
            return

        request_id = auth_req["request_id"]
        auth_url = auth_req["auth_url"]

        # 发送授权链接并获取 message_id
        link_msg = f"{get_message('enduid.auth_link_intro')}\n{auth_url}\n\n{get_message('enduid.auth_link_wait')}"
        client, link_message_id = await self._send_and_get_msg_id(
            event, [{"type": "text", "data": {"text": link_msg}}]
        )
        if link_message_id is None:
            yield event.plain_result(link_msg)

        # 调度 100 秒后自动撤回（兜底，确保在 QQ 2 分钟撤回窗口内）
        recall_task = None
        if client and link_message_id:
            recall_task = self._schedule_recall(client, link_message_id, 100)

        # 轮询授权状态，基于时间确保不超时
        start_time = time.time()
        auth_data = None
        while time.time() - start_time < 95:
            await asyncio.sleep(2)
            status = await self.client.get_authorization_request_status(request_id)
            if not status:
                continue

            state = status.get("status")
            if state in ["used", "approved"]:
                if status.get("framework_token"):
                    auth_data = status
                    # 认证成功，取消定时撤回，立即撤回
                    if recall_task and not recall_task.done():
                        recall_task.cancel()
                    if client and link_message_id:
                        try:
                            await client.delete_msg(message_id=link_message_id)
                            logger.info(
                                f"[撤回] 认证成功，已撤回链接消息 {link_message_id}"
                            )
                        except Exception as e:
                            logger.warning(f"[撤回] 认证成功后撤回失败: {e}")
                    break
            elif state == "rejected":
                if recall_task and not recall_task.done():
                    recall_task.cancel()
                if client and link_message_id:
                    try:
                        await client.delete_msg(message_id=link_message_id)
                    except:
                        pass
                yield event.plain_result(get_message("enduid.auth_rejected"))
                return
            elif state == "expired":
                if recall_task and not recall_task.done():
                    recall_task.cancel()
                if client and link_message_id:
                    try:
                        await client.delete_msg(message_id=link_message_id)
                    except:
                        pass
                yield event.plain_result(get_message("enduid.auth_expired"))
                return

        if not auth_data or not auth_data.get("framework_token"):
            # 超时，撤回由 recall_task 兜底处理
            yield event.plain_result("授权超时，请重新发起授权。")
            return

        # Create binding in unified backend
        token = auth_data["framework_token"]

        # 尝试提取角色信息以提高绑定成功率
        role_kwargs = {}
        roles = auth_data.get("available_roles", [])
        if roles:
            # 优先取默认角色，否则取第一个
            role = next((r for r in roles if r.get("is_default")), roles[0])
            role_kwargs = {
                "role_id": role.get("role_id"),
                "server_id": role.get("server_id"),
                "nickname": role.get("nickname"),
                "skland_uid": role.get("skland_uid"),
                "channel_name": role.get("channel_name"),
                "server_name": role.get("server_name"),
                "level": role.get("level"),
            }

        binding_res = await self.client.create_binding(token, user_id, **role_kwargs)

        if not binding_res:
            yield event.plain_result("创建绑定失败。")
            return

        # Save to local storage
        new_account = {
            "framework_token": token,
            "binding_id": binding_res.get("id"),
            "role_id": str(binding_res.get("role_id", "")),
            "nickname": binding_res.get("nickname", "未知"),
            "server_id": binding_res.get("server_id", 1),
            "channel_name": binding_res.get("channel_name")
            or (role_kwargs.get("channel_name") if "role_kwargs" in locals() else None),
            "server_name": binding_res.get("server_name")
            or (role_kwargs.get("server_name") if "role_kwargs" in locals() else None),
            "is_active": True,
            "is_primary": True,
            "login_type": "auth",
            "bind_time": int(time.time() * 1000),
            "last_sync": int(time.time() * 1000),
        }

        existing = await self.user_mgr.get_user_bindings(user_id)
        existing.append(new_account)
        await self.user_mgr.save_user_bindings(user_id, existing)

        yield event.plain_result(
            get_message(
                "enduid.login_ok",
                {
                    "nickname": new_account["nickname"],
                    "role_id": new_account["role_id"],
                    "server_id": self._get_server_name(new_account),
                    "count": len(await self.user_mgr.get_user_bindings(user_id)),
                },
            )
        )

    @filter.command("扫码绑定")
    async def qr_login(self, event: AstrMessageEvent):
        """扫码快捷登录"""
        user_id = event.get_sender_id()
        qr_data = await self.client.get_qr()
        if not qr_data or "qrcode" not in qr_data or "framework_token" not in qr_data:
            yield event.plain_result("获取二维码失败。")
            return

        token = qr_data["framework_token"]
        qr_b64 = qr_data["qrcode"]  # data:image/png;base64,...

        img_data = base64.b64decode(qr_b64.split(",")[-1])
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            tmp.write(img_data)
            tmp_path = tmp.name

        # 发送二维码并获取 message_id
        client, qr_message_id = await self._send_and_get_msg_id(
            event,
            [
                {
                    "type": "image",
                    "data": {"file": "base64://" + qr_b64.split(",")[-1]},
                },
                {
                    "type": "text",
                    "data": {
                        "text": "请使用森空岛 APP 扫描二维码进行登录。\n二维码有效时间约 1 分 40 秒。"
                    },
                },
            ],
        )
        if qr_message_id is None:
            yield event.chain_result(
                [
                    Image.fromFileSystem(tmp_path),
                    Plain(
                        "请使用森空岛 APP 扫描二维码进行登录。\n二维码有效时间约 1 分 40 秒。"
                    ),
                ]
            )

        # 调度 100 秒后自动撤回（兜底）
        recall_task = None
        if client and qr_message_id:
            recall_task = self._schedule_recall(client, qr_message_id, 100)

        # 轮询扫码状态，基于时间
        start_time = time.time()
        login_data = None

        try:
            while time.time() - start_time < 95:
                await asyncio.sleep(2)
                status = await self.client.get_qr_status(token)
                if not status:
                    continue

                if status.get("status") == "done":
                    login_data = await self.client.confirm_qr_login(token, user_id)
                    if login_data and login_data.get("framework_token"):
                        # 扫码成功，取消定时撤回，立即撤回
                        if recall_task and not recall_task.done():
                            recall_task.cancel()
                        if client and qr_message_id:
                            try:
                                await client.delete_msg(message_id=qr_message_id)
                                logger.info(
                                    f"[撤回] 扫码成功，已撤回二维码 {qr_message_id}"
                                )
                            except Exception as e:
                                logger.warning(f"[撤回] 扫码成功后撤回失败: {e}")
                        break
                elif status.get("status") in ["expired", "failed"]:
                    if recall_task and not recall_task.done():
                        recall_task.cancel()
                    if client and qr_message_id:
                        try:
                            await client.delete_msg(message_id=qr_message_id)
                        except:
                            pass
                    yield event.plain_result("二维码已过期或登录失败，请重新发起。")
                    return

            if not login_data:
                # 超时，撤回由 recall_task 兜底处理
                yield event.plain_result("扫码超时，请重新发起绑定。")
                return
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

        # Create binding
        auth_token = login_data["framework_token"]

        # 尝试提取角色信息以提高绑定成功率
        role_kwargs = {}
        roles = login_data.get("available_roles", [])
        if roles:
            # 优先取默认角色，否则取第一个
            role = next((r for r in roles if r.get("is_default")), roles[0])
            role_kwargs = {
                "role_id": role.get("role_id"),
                "server_id": role.get("server_id"),
                "nickname": role.get("nickname"),
                "skland_uid": role.get("skland_uid"),
                "channel_name": role.get("channel_name"),
                "server_name": role.get("server_name"),
                "level": role.get("level"),
            }

        binding_res = await self.client.create_binding(
            auth_token, user_id, **role_kwargs
        )
        if not binding_res:
            yield event.plain_result("创建绑定失败。")
            return

        # Save
        acc = {
            "framework_token": auth_token,
            "binding_id": binding_res.get("id"),
            "role_id": str(binding_res.get("role_id", "")),
            "nickname": binding_res.get("nickname", "未知"),
            "server_id": binding_res.get("server_id", 1),
            "channel_name": binding_res.get("channel_name")
            or role_kwargs.get("channel_name"),
            "server_name": binding_res.get("server_name")
            or role_kwargs.get("server_name"),
            "login_type": "qr",
            "is_active": True,
            "is_primary": True,
            "bind_time": int(time.time() * 1000),
            "last_sync": int(time.time() * 1000),
        }
        existing = await self.user_mgr.get_user_bindings(user_id)
        existing.append(acc)
        await self.user_mgr.save_user_bindings(user_id, existing)

        yield event.plain_result(
            get_message(
                "enduid.login_ok",
                {
                    "nickname": acc["nickname"],
                    "role_id": acc["role_id"],
                    "server_id": self._get_server_name(acc),
                    "count": len(await self.user_mgr.get_user_bindings(user_id)),
                },
            )
        )

    @filter.command("手机绑定")
    async def phone_login(self, event: AstrMessageEvent, phone: str):
        """手机号验证码登录"""
        if event.get_group_id():
            yield event.plain_result("手机号登录请在私聊中进行。")
            return

        success = await self.client.phone_send_code(phone)
        if not success:
            yield event.plain_result("发送验证码失败。")
            return

        yield event.plain_result(
            f"验证码已发送至 {phone[:3]}****{phone[7:]}，请输入 6 位验证码。"
        )

        # AstrBot Session Waiter
        from astrbot.core.utils.session_waiter import session_waiter, SessionController

        @session_waiter(timeout=300)
        async def waiter(controller: SessionController, waiter_event: AstrMessageEvent):
            code = waiter_event.message_str.strip()
            if not code.isdigit() or len(code) != 6:
                return  # Keep waiting

            login_data = await self.client.phone_verify_code(phone, code)
            if not login_data or not login_data.get("framework_token"):
                await waiter_event.send(
                    waiter_event.plain_result("验证码错误或登录失败。")
                )
                controller.stop()
                return

            token = login_data["framework_token"]

            # 尝试提取角色信息
            role_kwargs = {}
            roles = login_data.get("available_roles", [])
            if roles:
                role = next((r for r in roles if r.get("is_default")), roles[0])
                role_kwargs = {
                    "role_id": role.get("role_id"),
                    "server_id": role.get("server_id"),
                    "nickname": role.get("nickname"),
                    "skland_uid": role.get("skland_uid"),
                    "channel_name": role.get("channel_name"),
                    "server_name": role.get("server_name"),
                    "level": role.get("level"),
                }

            binding_res = await self.client.create_binding(
                token, event.get_sender_id(), **role_kwargs
            )
            if not binding_res:
                await waiter_event.send(waiter_event.plain_result("创建绑定失败。"))
                controller.stop()
                return

            # Save
            acc = {
                "framework_token": token,
                "binding_id": binding_res.get("id"),
                "role_id": str(binding_res.get("role_id", "")),
                "nickname": binding_res.get("nickname", "未知"),
                "server_id": binding_res.get("server_id", 1),
                "channel_name": binding_res.get("channel_name")
                or role_kwargs.get("channel_name")
                or ("官服" if str(binding_res.get("server_id")) == "1" else "B服"),
                "server_name": binding_res.get("server_name")
                or role_kwargs.get("server_name"),
                "login_type": "phone",
                "is_active": True,
                "is_primary": True,
                "bind_time": int(time.time() * 1000),
                "last_sync": 0,
            }
            existing = await self.user_mgr.get_user_bindings(event.get_sender_id())
            existing.append(acc)
            await self.user_mgr.save_user_bindings(event.get_sender_id(), existing)

            await waiter_event.send(
                waiter_event.plain_result(
                    get_message(
                        "enduid.login_ok",
                        {
                            "nickname": acc["nickname"],
                            "role_id": acc["role_id"],
                            "server_id": self._get_server_name(acc),
                            "count": len(
                                await self.user_mgr.get_user_bindings(
                                    event.get_sender_id()
                                )
                            ),
                        },
                    )
                )
            )
            controller.stop()

        try:
            await waiter(event)
        except TimeoutError:
            yield event.plain_result("验证超时。")

    @filter.command("国际服登录")
    async def skport_login_command(self, event: AstrMessageEvent):
        """国际服账号密码登录 (skport)"""
        if event.get_group_id():
            yield event.plain_result(
                "请在群聊以外的私聊窗口私聊机器人执行此指令，以防密码泄露。"
            )
            return

        self.client.set_caller(
            bot_qq=event.get_self_id(), user_qq=event.get_sender_id()
        )
        yield event.plain_result(
            "请输入您的 skport (国际服) 账号邮箱：\n(输入 '取消' 退出)"
        )

        from astrbot.core.utils.session_waiter import session_waiter, SessionController

        email = None

        @session_waiter(timeout=120)
        async def email_waiter(
            controller: SessionController, waiter_event: AstrMessageEvent
        ):
            nonlocal email
            text = waiter_event.message_str.strip()
            if text == "取消":
                await waiter_event.send(waiter_event.plain_result("已取消登录。"))
                controller.stop()
                return

            email = text
            warning_msg = "请继续输入该 skport 账号的密码：\n(输入 '取消' 退出)\n\n⚠️ 警告：机器人无法代为撤回您的密码消息。请您在验证完成后务必【自行撤回】您的密码，否则后果自负！"
            await waiter_event.send(waiter_event.plain_result(warning_msg))
            controller.stop()

        try:
            await email_waiter(event)
        except TimeoutError:
            yield event.plain_result("输入邮箱超时。")
            return

        if not email:
            return

        @session_waiter(timeout=120)
        async def pwd_waiter(
            controller: SessionController, pwd_event: AstrMessageEvent
        ):
            password = pwd_event.message_str.strip()
            if password == "取消":
                await pwd_event.send(pwd_event.plain_result("已取消登录。"))
                controller.stop()
                return

            await pwd_event.send(
                pwd_event.plain_result("正在验证国际服凭证，请稍候...")
            )
            res = await self.client.login_skport_password(email, password)

            if not res or not res.get("framework_token"):
                await pwd_event.send(
                    pwd_event.plain_result(
                        "登录失败：账号或密码错误。目前插件暂不支持在此阶段处理极验证码，若持续失败请先在网页端登录并完成验证码后重试。\n\n⚠️ 验证失败，请务必【自行撤回】您的密码消息！"
                    )
                )
                controller.stop()
                return

            token = res["framework_token"]
            roles = res.get("available_roles", [])

            if not roles:
                await pwd_event.send(
                    pwd_event.plain_result(
                        "登录成功，但未发现包含游戏角色的记录。\n\n⚠️ 验证结束，请务必【自行撤回】您的密码消息！"
                    )
                )
                controller.stop()
                return

            # Default to the first role or the default one
            role = next((r for r in roles if r.get("is_default")), roles[0])
            role_id = role.get("role_id")

            # Bind with provider="skport"
            binding_res = await self.client.create_binding(
                framework_token=token,
                user_id=event.get_sender_id(),
                role_id=role_id,
                server_id=role.get("server_id", 1),
                nickname=role.get("nickname", ""),
                skland_uid=role.get("skland_uid", ""),
                channel_name=role.get("channel_name", ""),
                level=role.get("level", 0),
                provider="skport",
            )

            if not binding_res:
                await pwd_event.send(
                    pwd_event.plain_result(
                        "创建国际服绑定失败。\n\n⚠️ 验证结束，请务必【自行撤回】您的密码消息！"
                    )
                )
                controller.stop()
                return

            # Save to user_mgr
            acc = {
                "framework_token": token,
                "binding_id": binding_res.get("id")
                or binding_res.get("binding_id")
                or role_id,
                "role_id": str(role_id),
                "nickname": binding_res.get("nickname")
                or role.get("nickname")
                or "未知",
                "server_id": binding_res.get("server_id") or role.get("server_id") or 1,
                "channel_name": binding_res.get("channel_name")
                or role.get("channel_name")
                or "SKPORT",
                "server_name": binding_res.get("server_name")
                or role.get("server_name"),
                "login_type": "skport",
                "is_active": True,
                "is_primary": True,
                "bind_time": int(time.time() * 1000),
                "last_sync": 0,
            }

            # Make all other accounts non-primary
            existing = await self.user_mgr.get_user_bindings(event.get_sender_id())
            for e in existing:
                e["is_primary"] = False

            existing.append(acc)
            await self.user_mgr.save_user_bindings(event.get_sender_id(), existing)

            ok_msg = get_message(
                "enduid.login_ok",
                {
                    "nickname": acc["nickname"],
                    "role_id": acc["role_id"],
                    "server_id": acc["channel_name"],
                    "count": len(existing),
                },
            )
            await pwd_event.send(
                pwd_event.plain_result(
                    ok_msg + "\n\n⚠️ 验证完成，请务必【自行撤回】您的密码消息！"
                )
            )
            controller.stop()

        try:
            await pwd_waiter(event)
        except TimeoutError:
            yield event.plain_result("密码输入超时。")

    @filter.command("理智")
    async def stamina(self, event: AstrMessageEvent):
        """查询理智状态"""
        user_id = event.get_sender_id()
        binding = await self.user_mgr.get_primary_binding(user_id)
        if not binding:
            yield event.plain_result("未绑定账号，请输入 /zmd 查看绑定方式。")
            return

        yield event.plain_result(get_message("stamina.loading"))

        token = binding.get("framework_token")
        role_id = binding.get("role_id")
        server_id = binding.get("server_id", 1)

        stamina_data = await self.client.get_stamina(token, role_id, server_id)
        if not stamina_data:
            yield event.plain_result(get_message("common.get_role_failed"))
            return

        note_data = await self.client.get_note(token, role_id, server_id)

        stamina_obj = stamina_data.get("stamina", {})
        daily_obj = stamina_data.get("dailyMission", {})
        role_obj = stamina_data.get("role", {})

        s_current = int(stamina_obj.get("current", 0) or 0)
        s_max = int(stamina_obj.get("max", 0) or 0)
        s_maxTs = int(stamina_obj.get("maxTs", 0) or 0)
        s_recover = int(stamina_obj.get("recover", 360) or 360)
        a_current = int(daily_obj.get("activation", 0) or 0)
        a_max = int(daily_obj.get("maxActivation", 100) or 100)

        if s_current >= s_max and s_max > 0:
            full_time = "已满"
        elif s_maxTs > 0:
            full_time = time.strftime("%Y-%m-%d %H:%M", time.localtime(s_maxTs))
        elif s_current < s_max and s_recover > 0:
            remaining = s_max - s_current
            recover_seconds = remaining * s_recover
            full_time = time.strftime(
                "%H:%M", time.localtime(time.time() + recover_seconds)
            )
        else:
            full_time = "未知"

        user_name = binding.get("nickname")
        user_level = 0
        user_avatar = binding.get("avatarUrl", "")
        if note_data:
            base = note_data.get("base", {})
            user_name = base.get("name") or role_obj.get("name") or user_name
            user_level = int(base.get("level", 0) or role_obj.get("level", 0) or 0)
            user_avatar = base.get("avatarUrl") or user_avatar
        else:
            user_name = role_obj.get("name") or user_name
            user_level = int(role_obj.get("level", 0) or 0)

        acc_data = {
            "userName": user_name,
            "userUid": role_id,
            "userLevel": user_level,
            "userAvatar": await self.get_b64(user_avatar) if user_avatar else "",
            "current": s_current,
            "max": s_max,
            "staminaPercent": s_current / max(s_max, 1),
            "fullTime": full_time,
            "activation": a_current,
            "maxActivation": a_max,
            "activationPercent": (a_current / max(a_max, 1)) * 100,
        }

        op_dir = os.path.join(self.renderer.res_path, "img", "operator")
        if os.path.exists(op_dir):
            ops = [
                f for f in os.listdir(op_dir) if f.endswith((".png", ".jpg", ".webp"))
            ]
            if ops:
                import random

                op_file = random.choice(ops)
                acc_data["operatorImg"] = await self.get_b64(
                    os.path.join("img", "operator", op_file)
                )

        acc_data["staminaBgImg"] = await self.get_b64("img/stbg.png")
        acc_data["server_label"] = self._get_server_name(binding)

        render_data = {
            "accounts": [acc_data],
            "pluResPath": "file:///"
            + os.path.abspath(self.renderer.res_path).replace("\\", "/")
            + "/",
            "copyright": "Endfield Plugin | AstrBot",
        }

        try:
            img_url = await self.renderer.render_html(
                "stamina/stamina.html", render_data
            )
            if img_url:
                yield event.image_result(img_url)
                return
        except Exception as e:
            logger.warning(f"渲染理智失败，使用文本回退: {e}")
        yield event.plain_result(
            f"【{acc_data['userName']}】\n理智：{acc_data['current']}/{acc_data['max']}\n活跃度：{acc_data['activation']}/{acc_data['maxActivation']}\n回满时间：{acc_data['fullTime']}"
        )

    @filter.command("便签")
    async def note(self, event: AstrMessageEvent):
        """查询角色便签"""
        user_id = event.get_sender_id()
        binding = await self.user_mgr.get_primary_binding(user_id)
        if not binding:
            yield event.plain_result("未绑定账号，请输入 /zmd 查看绑定方式。")
            return

        yield event.plain_result("正在获取实时便签...")

        token = binding.get("framework_token")
        role_id = binding.get("role_id")
        server_id = binding.get("server_id", 1)

        note_data = await self.client.get_note(token, role_id, server_id)
        if not note_data or "base" not in note_data:
            yield event.plain_result(get_message("common.get_role_failed"))
            return

        stamina_data = await self.client.get_stamina(token, role_id, server_id)

        base = note_data.get("base", {})
        chars = note_data.get("chars", [])
        achieve = note_data.get("achieve", {})
        bp_system = note_data.get("bpSystem", {})

        # Use unified server name logic
        temp_acc = {
            **binding,
            "server_name": base.get("serverName") or binding.get("server_name"),
        }
        server_name = self._get_server_name(temp_acc)

        stamina_obj = stamina_data.get("stamina", {}) if stamina_data else {}
        daily_obj = stamina_data.get("dailyMission", {}) if stamina_data else {}

        s_current = stamina_obj.get("current", "—")
        s_max = stamina_obj.get("max", "—")
        a_current = daily_obj.get("activation", "—")
        a_max = daily_obj.get("maxActivation", 100)

        create_time = base.get("createTime")
        awakening_date_str = (
            time.strftime("%Y-%m-%d", time.localtime(int(create_time)))
            if create_time
            else ""
        )
        create_time_str = ""
        if create_time:
            try:
                create_time_str = time.strftime(
                    "%Y/%m/%d %H:%M:%S", time.localtime(int(create_time))
                )
            except (ValueError, OSError, TypeError):
                create_time_str = awakening_date_str
        last_online = (
            base.get("lastLoginTime")
            or base.get("lastOnline")
            or base.get("lastOnlineTime")
        )
        last_online_str = ""
        if last_online:
            try:
                last_online_str = time.strftime(
                    "%Y/%m/%d %H:%M:%S", time.localtime(int(last_online))
                )
            except (ValueError, OSError, TypeError):
                pass
        weekly_obj = stamina_data.get("weeklyMission", {}) if stamina_data else {}
        weekly_score = weekly_obj.get("score", 0) or weekly_obj.get("activation", 0)
        weekly_max = weekly_obj.get("maxScore", 10) or weekly_obj.get(
            "maxActivation", 10
        )

        # Parallel download icons
        image_urls = [base.get("avatarUrl", "")]
        for c in chars:
            image_urls.append(c.get("avatarSqUrl") or c.get("avatar_sq_url") or "")

        # Add achievement medals (limit to 10 for layout)，优先展示“更难”的成就
        medals = achieve.get("achieveMedals", [])
        if medals:
            medals = sorted(medals, key=self._medal_sort_key, reverse=True)
        processed_medals = []

        # 根据实际返回结构（见测试文件 achieve_from_note.json / achieve_direct.json）,
        # 勋章图标字段位于 achievementData 中：initIcon / reforge2Icon / reforge3Icon / platedIcon
        medal_image_urls = []
        for m in medals[:10]:
            data = m.get("achievementData", {}) or {}
            level = m.get("level") or data.get("initLevel") or 1
            is_plated = m.get("isPlated", False)

            icon_url = ""
            # 已镀层优先用 platedIcon
            if is_plated and data.get("platedIcon"):
                icon_url = data.get("platedIcon")
            else:
                # 等级 3 尝试使用 reforge3Icon，其次 reforge2Icon，最后 initIcon
                if level >= 3 and data.get("reforge3Icon"):
                    icon_url = data.get("reforge3Icon")
                elif level >= 2 and data.get("reforge2Icon"):
                    icon_url = data.get("reforge2Icon")
                else:
                    icon_url = (
                        data.get("initIcon")
                        or data.get("platedIcon")
                        or data.get("reforge2Icon")
                        or data.get("reforge3Icon")
                        or ""
                    )

            medal_image_urls.append(icon_url)

        medal_paths = await self.parallel_download_b64(
            [u for u in medal_image_urls if u]
        )

        mi = 0
        for i, m in enumerate(medals[:10]):
            ad = m.get("achievementData")
            if ad is None:
                ad = {}
            # Copy name/cateName immediately so shared API dict reference doesn't overwrite
            name_str = str(ad.get("name") or "未知").strip().strip('"').strip('"')
            cate_str = str(ad.get("cateName") or "")
            icon_url = medal_image_urls[i]
            local_path = ""
            if icon_url:
                local_path = medal_paths[mi]
                mi += 1
            processed_medals.append(
                {
                    "name": name_str,
                    "level": m.get("level", 1),
                    "isPlated": m.get("isPlated", False),
                    "icon": local_path,
                    "initial": (name_str[0] if name_str else " "),
                    "cateName": cate_str,
                }
            )

        local_paths = await self.parallel_download_b64([u for u in image_urls if u])

        user_avatar = local_paths[0] if image_urls[0] else ""
        char_paths = local_paths[1:] if image_urls[0] else local_paths

        # Progress bar percentages (0-100, cap at 100)
        try:
            a_num, a_den = (
                int(a_current) if str(a_current).isdigit() else 0,
                int(a_max) if str(a_max).isdigit() else 100,
            )
            progress_daily_pct = min(100, int(100 * a_num / a_den) if a_den else 0)
        except (ValueError, TypeError):
            progress_daily_pct = 0
        try:
            w_den = int(weekly_max) if str(weekly_max).isdigit() else 10
            w_num = min(int(weekly_score) if str(weekly_score).isdigit() else 0, w_den)
            progress_weekly_pct = min(100, int(100 * w_num / w_den) if w_den else 0)
        except (ValueError, TypeError):
            progress_weekly_pct = 0
        bp_cur = bp_system.get("curLevel", 0)
        bp_max = bp_system.get("maxLevel", 60)
        try:
            progress_bp_pct = min(
                100, int(100 * int(bp_cur) / int(bp_max)) if int(bp_max) else 0
            )
        except (ValueError, TypeError):
            progress_bp_pct = 0

        chars_list = []
        for i, c in enumerate(chars):
            level = c.get("level") or c.get("charLevel")
            star_raw = c.get("rarity") or c.get("star") or c.get("rarityLevel")
            # 兼容接口里 rarity 既可能是数字/字符串，也可能是 {key: 'rarity_5', value: '5'} 这种结构
            star = star_raw
            if isinstance(star_raw, dict):
                star = star_raw.get("value") or star_raw.get("label")
                if not star:
                    key = str(star_raw.get("key") or "")
                    if key.startswith("rarity_"):
                        star = key.replace("rarity_", "", 1)
            chars_list.append(
                {
                    "name": c.get("name", "未知"),
                    "sqUrl": char_paths[i] if i < len(char_paths) else "",
                    "level": level,
                    "star": star,
                }
            )

        # Pick random bg from already-downloaded file:// paths for medal & chars panels
        def _pick_random_bg(values: list) -> str:
            candidates = [v for v in values if v and isinstance(v, str)]
            return random.choice(candidates) if candidates else ""

        medal_panel_bg = _pick_random_bg([m.get("icon", "") for m in processed_medals])
        chars_panel_bg = _pick_random_bg([c.get("sqUrl", "") for c in chars_list])

        render_data = {
            "title": "终末地便签",
            "subtitle": f"{base.get('name', '未知')} · {server_name}",
            "base": {
                "name": base.get("name", "未知"),
                "roleId": base.get("roleId", "未知"),
                "level": base.get("level", 0),
                "worldLevel": base.get("worldLevel", 0),
                "serverName": server_name,
                "mainMissionDesc": base.get("mainMission", {}).get(
                    "description", "自由开拓"
                ),
                "avatarUrl": user_avatar,
                "awakeningDateStr": awakening_date_str,
                "createTimeStr": create_time_str,
                "lastOnlineStr": last_online_str or None,
            },
            "stats": {
                "charNum": base.get("charNum", 0),
                "weaponNum": base.get("weaponNum", 0),
                "docNum": base.get("docNum", 0),
                "achieveCount": achieve.get("count", 0),
                "bpLevel": bp_system.get("curLevel", 0),
                "bpMax": bp_system.get("maxLevel", 60),
                "staminaCurrent": s_current,
                "staminaMax": s_max,
                "activation": a_current,
                "maxActivation": a_max,
                "progressDailyPct": progress_daily_pct,
                "progressWeeklyPct": progress_weekly_pct,
                "progressBpPct": progress_bp_pct,
                "weeklyScore": weekly_score,
                "weeklyMax": weekly_max,
                "registerNum": base.get("registerNum") or base.get("regNum"),
                "teamCount": base.get("teamCount") or note_data.get("teamCount"),
            },
            "achievements": processed_medals,
            "chars": chars_list,
            "charNum": len(chars_list),
            "medalPanelBg": medal_panel_bg,
            "charsPanelBg": chars_panel_bg,
            "pluResPath": "file:///"
            + os.path.abspath(self.renderer.res_path).replace("\\", "/")
            + "/",
        }

        try:
            img_url = await self.renderer.render_html("note/note.html", render_data)
            if img_url:
                yield event.image_result(img_url)
                return
        except Exception as e:
            logger.warning(f"渲染便签失败，使用文本回退: {e}")

        msg = f"角色名：{base.get('name', '未知')}\n理智：{s_current}/{s_max}\n活跃度：{a_current}/{a_max}"
        yield event.plain_result(msg)

    @filter.command("签到")
    async def attendance(self, event: AstrMessageEvent):
        """每日签到"""
        user_id = event.get_sender_id()
        all_bindings = await self.user_mgr.get_user_bindings(user_id)
        if not all_bindings:
            yield event.plain_result("未绑定账号。")
            return

        results = []
        for b in all_bindings:
            label = b.get("nickname") or b.get("role_id")
            token = b.get("framework_token")
            res = await self.client.get_attendance(token)
            if not res:
                results.append(f"【{label}】签到失败或今日已签到（插件每日自动签到）。")
                continue

            if res.get("already_signed"):
                results.append(f"【{label}】今日已签到。")
                continue

            awards = res.get("awardIds", [])
            info_map = res.get("resourceInfoMap", {})
            award_msg = ""
            for a in awards:
                item = info_map.get(str(a.get("id")), {})
                award_msg += f"\n  {item.get('name', '未知')} * {item.get('count', a.get('count', 0))}"
            results.append(f"【{label}】签到成功！获得:{award_msg}")

        yield event.plain_result("\n".join(results))

    @filter.command("成就列表", alias=["成就查询", "成就", "成就一览"])
    async def achieve_cmd(self, event: AstrMessageEvent):
        """查询全部成就徽章并生成单独面板"""
        user_id = event.get_sender_id()
        binding = await self.user_mgr.get_primary_binding(user_id)
        if not binding:
            yield event.plain_result("未绑定账号，请输入 /zmd 查看绑定方式。")
            return

        token = binding.get("framework_token")
        role_id = binding.get("role_id")
        server_id = int(binding.get("server_id", 1))

        yield event.plain_result("正在获取成就信息...")

        # 便签基础信息用于顶部头像/昵称
        note_data = await self.client.get_note(token, role_id, server_id)
        achieve_res = await self.client.get_achieve(token, role_id, server_id)

        if not achieve_res:
            yield event.plain_result("获取成就信息失败。")
            return

        base = (note_data or {}).get("base", {}) if isinstance(note_data, dict) else {}
        achieve = achieve_res.get("achieve") or achieve_res
        medals = achieve.get("achieveMedals", [])
        if not medals:
            yield event.plain_result("当前账号暂无成就徽章数据。")
            return

        # 排序：与便签一致，优先显示更高难度/等级与最近获得的成就
        medals_sorted = sorted(medals, key=self._medal_sort_key, reverse=True)

        # 为所有成就下载图标
        medal_image_urls = []
        for m in medals_sorted:
            data = m.get("achievementData", {}) or {}
            level = m.get("level") or data.get("initLevel") or 1
            is_plated = m.get("isPlated", False)
            icon_url = ""
            if is_plated and data.get("platedIcon"):
                icon_url = data.get("platedIcon")
            else:
                if level >= 3 and data.get("reforge3Icon"):
                    icon_url = data.get("reforge3Icon")
                elif level >= 2 and data.get("reforge2Icon"):
                    icon_url = data.get("reforge2Icon")
                else:
                    icon_url = (
                        data.get("initIcon")
                        or data.get("platedIcon")
                        or data.get("reforge2Icon")
                        or data.get("reforge3Icon")
                        or ""
                    )
            medal_image_urls.append(icon_url)

        medal_paths = await self.parallel_download_b64(
            [u for u in medal_image_urls if u]
        )

        processed_medals = []
        mi = 0
        for idx, m in enumerate(medals_sorted):
            ad = m.get("achievementData")
            if ad is None:
                ad = {}
            name = str(ad.get("name") or "未知").strip().strip('"').strip('"')
            cate_str = str(ad.get("cateName") or "")
            level = m.get("level", 1)
            is_plated = m.get("isPlated", False)
            icon_url = medal_image_urls[idx]
            local_path = ""
            if icon_url and mi < len(medal_paths):
                local_path = medal_paths[mi]
                mi += 1
            obtain_ts = m.get("obtainTs")
            date_str = ""
            if obtain_ts:
                try:
                    date_str = datetime.datetime.fromtimestamp(int(obtain_ts)).strftime(
                        "%Y-%m-%d"
                    )
                except Exception:
                    date_str = ""

            processed_medals.append(
                {
                    "name": name,
                    "level": level,
                    "isPlated": is_plated,
                    "icon": local_path,
                    "initial": name[0] if name else "",
                    "cateName": cate_str,
                    "date": date_str,
                }
            )

        render_data = {
            "userAvatar": base.get("avatarUrl", ""),
            "userName": base.get("name", binding.get("nickname", "未知")),
            "userUid": base.get("roleId", binding.get("role_id", "0")),
            "userLevel": base.get("level", binding.get("level", 0)),
            "achieveCount": achieve.get("count", len(processed_medals)),
            "medals": processed_medals,
            "pluResPath": "file:///"
            + os.path.abspath(self.renderer.res_path).replace("\\", "/")
            + "/",
            "copyright": "AstrBot · Endfield Plugin",
        }

        url = await self.renderer.render_html(
            "achievement/achievement.html", render_data
        )
        if url:
            yield event.image_result(url)
        else:
            # 文本回退：只列出前 30 个成就名
            lines = [f"成就总数：{achieve.get('count', len(processed_medals))}"]
            for m in processed_medals[:30]:
                lines.append(f"- {m['name']}（等级{m['level']}）")
            yield event.plain_result("\n".join(lines))

    @filter.command("干员列表")
    async def operator_list(self, event: AstrMessageEvent):
        """查询干员列表"""
        user_id = event.get_sender_id()
        binding = await self.user_mgr.get_primary_binding(user_id)
        if not binding:
            yield event.plain_result("未绑定账号。")
            return

        yield event.plain_result("正在查询干员列表...")

        token = binding.get("framework_token")
        res = await self.client.get_card_detail(
            token, binding.get("role_id"), binding.get("server_id", 1)
        )

        if not res or "detail" not in res:
            yield event.plain_result("查询失败。")
            return

        detail = res["detail"]
        chars = detail.get("chars", [])
        if not chars:
            yield event.plain_result("未查询到干员数据。")
            return

        color_codes = {
            "PHYSICAL": "PHY",
            "ARTS": "ART",
            "TRUE": "TRU",
            "STRIKER": "STR",
            "CASTER": "CAS",
            "SUPPORT": "SUP",
            "DEFENDER": "DEF",
            "SNIPER": "SNI",
            "MEDIC": "MED",
            "VANGUARD": "VAN",
            "SPECIALIST": "SPE",
        }

        # Pre-download all operator illustrations in parallel
        illustration_urls = [
            char_data.get("avatarRtUrl", "")
            for c in chars
            for char_data in [c.get("charData", c)]
        ]
        local_illustrations = await self.parallel_download_b64(illustration_urls)

        operators = []
        for i, c in enumerate(chars):
            char_data = c.get("charData", c)
            prof = char_data.get("profession", {}).get("value", "")
            prop = char_data.get("property", {}).get("value", "")
            operators.append(
                {
                    "name": char_data.get("name", "未知"),
                    "nameChars": list(char_data.get("name", "未知")),
                    "rarity": int(char_data.get("rarity", {}).get("value", 1))
                    if isinstance(char_data.get("rarity"), dict)
                    else 1,
                    "level": c.get("level", 0),
                    "imageUrl": local_illustrations[i],
                    "profession": prof,
                    "property": prop,
                    "professionIcon": await self.get_b64(f"meta/class/{prof}.jpg")
                    if prof
                    else "",
                    "propertyIcon": await self.get_b64(f"meta/attrpanle/{prop}.jpg")
                    if prop
                    else "",
                    "phaseIcon": await self.get_b64(
                        f"meta/phases/phase-{c.get('evolvePhase', 0)}.png"
                    ),
                    "potentialLevel": c.get("potentialLevel", 0),
                    "colorCode": color_codes.get(prop, "PHY"),
                }
            )

        operators.sort(key=lambda x: (x["rarity"], x["level"]), reverse=True)

        # Calculate layout constraints
        list_card_width = 300
        list_column_count = 6
        list_gap_px = 12
        list_content_width = (list_card_width * list_column_count) + (
            list_gap_px * (list_column_count - 1)
        )
        list_page_width = (
            list_content_width + 56
        )  # 28px padding on each side (matches top-bar)

        # Get background from config
        import random

        list_bg_cfg = self.config.get("operator_list_bg", "random")
        if list_bg_cfg == "random":
            list_bg_file = random.choice(["bg1.png", "bg2.png"])
        else:
            list_bg_file = list_bg_cfg

        render_data = {
            "totalCount": len(operators),
            "operators": operators,
            "userNickname": detail.get("base", {}).get("name", binding.get("nickname")),
            "userLevel": detail.get("base", {}).get("level", 0),
            "userAvatar": await self.get_b64(
                detail.get("base", {}).get("avatarUrl", "")
            ),
            "listBgFile": list_bg_file,
            "pluResPath": "file:///"
            + os.path.abspath(self.renderer.res_path).replace("\\", "/")
            + "/",
            "copyright": "Endfield Plugin | AstrBot",
            "listPageWidth": list_page_width,
            "listContentWidth": list_content_width,
            "listColumnCount": list_column_count,
            "listCardWidthPx": list_card_width,
            "listGapPx": list_gap_px,
            "listCardScale": list_card_width / 800.0,
        }

        try:
            img_url = await self.renderer.render_html("operator/list.html", render_data)
            if img_url:
                yield event.image_result(img_url)
                return
        except Exception as e:
            logger.warning(f"渲染干员列表失败，使用文本回退: {e}")
        msg = f"【{render_data['userNickname']} 的干员列表 ({len(operators)})】\n"
        for op in operators[:20]:
            msg += f"Lv.{op['level']} {op['name']} ({op['rarity']}星)\n"
        if len(operators) > 20:
            msg += "..."
        yield event.plain_result(msg)

    @filter.regex(r"^\*?(?:终末地)?\s*(.+?)\s*(?:终末地)?面板$")
    async def operator_panel(self, event: AstrMessageEvent):
        """查询干员详细面板"""
        import re

        msg = event.get_message_str().strip()
        m = re.match(r"^\*?(?:终末地)?\s*(.+?)\s*(?:终末地)?面板$", msg)
        char_name = m.group(1).strip() if m else ""
        if not char_name:
            yield event.plain_result("请指定干员名称，例如：莱万汀面板")
            return

        # Guard: ignore reserved system command keywords
        if char_name in {
            "同步",
            "绑定",
            "理智",
            "便签",
            "签到",
            "日历",
            "公告",
            "菜单",
            "帮助",
        }:
            return

        user_id = event.get_sender_id()
        binding = await self.user_mgr.get_primary_binding(user_id)
        if not binding:
            yield event.plain_result("未绑定账号。")
            return

        yield event.plain_result(f"正在查询 {char_name} 的面板...")

        token = binding.get("framework_token")
        note = await self.client.get_note(
            token, binding["role_id"], int(binding.get("server_id", 1))
        )
        if not note or "chars" not in note:
            yield event.plain_result("获取干员列表失败。")
            return

        matched = next(
            (c for c in note["chars"] if c.get("name", "") == char_name), None
        )
        if not matched:
            matched = next(
                (c for c in note["chars"] if char_name in c.get("name", "")), None
            )
        if not matched:
            yield event.plain_result(f"未在当前账号找到干员 {char_name}。")
            return

        inst_id = matched.get("id")
        full_res = await self.client.get_card_char(token, inst_id)
        if not full_res:
            yield event.plain_result("获取面板详情失败。")
            return

        # Fetch synced panel data for combat stats
        template_id = matched.get("template_id") or matched.get("templateId")
        if not template_id:
            panel_chars_res = await self.client.get_panel_chars(token)
            if panel_chars_res and "synced_chars" in panel_chars_res:
                for pc in panel_chars_res["synced_chars"]:
                    pc_name = pc.get("name_cn") or pc.get("name", "")
                    if (
                        pc_name == char_name
                        or char_name in pc_name
                        or pc_name in char_name
                    ):
                        template_id = pc.get("template_id")
                        break

        panel_stats = {"summary": {}}
        if template_id:
            panel_res = await self.client.get_panel_char(token, template_id)
            if panel_res:
                p = panel_res.get("panel") if "panel" in panel_res else panel_res
                if p and "summary" in p:
                    panel_stats = p

        try:
            render_data = self._prepare_operator_render_data(
                full_res, panel_stats, binding, matched
            )
            url = await self.renderer.render_html("operator/operator.html", render_data)
            if url:
                yield event.image_result(url)
            else:
                yield event.plain_result("图片渲染失败。")
        except Exception as e:
            logger.error(f"Error rendering operator panel for {char_name}: {e}")
            yield event.plain_result(f"渲染面板出错: {e}")

    @filter.command("同步面板")
    async def sync_panel(self, event: AstrMessageEvent):
        """同步干员面板数据（用于获取攻击力等战斗属性）"""
        user_id = event.get_sender_id()
        binding = await self.user_mgr.get_primary_binding(user_id)
        if not binding:
            yield event.plain_result("未绑定账号，请先绑定。")
            return

        token = binding.get("framework_token")

        # Trigger sync
        sync_res = await self.client.sync_panel(token)
        if not sync_res:
            yield event.plain_result("❌ 触发面板同步失败，请稍后再试。")
            return

        yield event.plain_result("🔄 面板同步已提交，请稍候...")

        # Poll silently until done
        import asyncio as _asyncio

        max_wait = 90  # max 90s
        elapsed = 0
        interval = 3
        while elapsed < max_wait:
            await _asyncio.sleep(interval)
            elapsed += interval
            status_res = await self.client.get_panel_sync_status(token)
            if not status_res:
                continue
            status = status_res.get("status", "")
            total = status_res.get("total", 0)
            failed = status_res.get("failed_ids", [])

            if status == "completed" or status == "idle":
                # Fetch synced character list for names
                chars_res = await self.client.get_panel_chars(token)
                char_names = []
                if chars_res and "synced_chars" in chars_res:
                    char_names = [
                        c.get("name_cn") or c.get("name") or "?"
                        for c in chars_res["synced_chars"]
                    ]

                msg = f"✅ 面板同步完成！共同步 {total or len(char_names)} 名干员。"
                if char_names:
                    msg += "\n" + "、".join(char_names)
                if failed:
                    msg += f"\n⚠️ {len(failed)} 名干员同步失败。"
                yield event.plain_result(msg)
                return
            elif status == "failed":
                yield event.plain_result("❌ 面板同步失败，请稍后重试。")
                return
            # syncing / pending: keep polling silently

        yield event.plain_result(
            "⏱ 同步超时，数据可能已在后台更新，稍后查看干员面板即可。"
        )

    @filter.command("抽卡记录")
    async def gacha_records(self, event: AstrMessageEvent, page: int = 1):
        """查询抽卡记录"""
        user_id = event.get_sender_id()
        binding = await self.user_mgr.get_primary_binding(user_id)
        if not binding:
            yield event.plain_result("未绑定账号。")
            return

        token = binding.get("framework_token")
        stats = await self.client.get_gacha_stats(token)
        if not stats:
            yield event.plain_result("查询抽卡统计失败。")
            return

        # Basic text result for now, graphical rendering is complex
        res_stats = stats.get("stats", {})
        msg = f"【抽卡记录 - {binding.get('nickname')}】\n"
        msg += f"总抽数：{res_stats.get('total_count', 0)}\n"
        msg += f"六星：{res_stats.get('star6_count', 0)} | 五星：{res_stats.get('star5_count', 0)} | 四星：{res_stats.get('star4_count', 0)}\n"

        # Fetch one page of records
        records_res = await self.client.get_gacha_records(token, page=page, limit=10)

        # Try to render gacha-record.html
        render_data = {
            "title": "抽卡记录",
            "totalCount": res_stats.get("total_count", 0),
            "star6": res_stats.get("star6_count", 0),
            "star5": res_stats.get("star5_count", 0),
            "star4": res_stats.get("star4_count", 0),
            "userNickname": binding.get("nickname") or "未知",
            "userUid": binding.get("role_id", ""),
            "userAvatar": await self.get_b64(binding.get("avatarUrl", "")),
            "page": page,
            "pageSize": 10,
            "poolSections": [],
            "copyright": "Endfield Plugin | AstrBot",
        }

        if records_res and "records" in records_res:
            render_data["poolSections"].append(
                {
                    "label": "最近记录",
                    "total": records_res.get("total", len(records_res["records"])),
                    "page": page,
                    "pages": records_res.get("pages", 1),
                    "hasRecords": len(records_res["records"]) > 0,
                    "records": [
                        {
                            "index": i + 1 + (page - 1) * 10,
                            "rarity": r.get("rarity"),
                            "starClass": f"star{r.get('rarity')}",
                            "name": r.get("char_name") or r.get("item_name"),
                            "isUp": False,
                        }
                        for i, r in enumerate(records_res["records"])
                    ],
                }
            )

            try:
                img_url = await self.renderer.render_html(
                    "gacha/gacha-record.html", render_data
                )
                if img_url:
                    yield event.image_result(img_url)
                    return
            except Exception as e:
                logger.warning(f"渲染抽卡记录失败，使用文本回退: {e}")

            msg += f"\n最近记录 (第 {page} 页):\n"
            for r in records_res["records"]:
                msg += (
                    f"- ★{r.get('rarity')} {r.get('char_name') or r.get('item_name')}\n"
                )

        yield event.plain_result(msg)

    @filter.command("抽卡分析同步")
    async def gacha_sync(self, event: AstrMessageEvent):
        """同步抽卡记录并分析"""
        user_id = event.get_sender_id()
        binding = await self.user_mgr.get_primary_binding(user_id)
        if not binding:
            yield event.plain_result("未绑定账号。请先使用 /zmd 查看绑定。")
            return

        token = binding.get("framework_token")
        role_id = binding.get("role_id")

        yield event.plain_result("正在启动抽卡记录同步，请稍候...")
        res = await self.client.post_gacha_fetch(token, role_id=role_id)

        if res and res.get("status") == "conflict":
            yield event.plain_result("服务器正在同步您的数据，请稍后再试。")
            return

        # Poll sync status
        import asyncio

        for _ in range(60):
            await asyncio.sleep(2)
            status_res = await self.client.get_gacha_sync_status(token)
            if not status_res:
                continue

            status = status_res.get("status")
            if status == "completed":
                new_records = status_res.get("new_records", 0)
                yield event.plain_result(
                    f"同步完成！拉取到 {new_records} 条新记录。正在生成分析图..."
                )
                # Call analysis
                async for res in self.gacha_analysis(event):
                    yield res
                return
            elif status == "failed":
                yield event.plain_result(
                    f"同步失败: {status_res.get('error', '未知错误')}"
                )
                return

        yield event.plain_result(
            "同步耗时过长，已在后台继续进行，请稍后使用【/抽卡分析】查看。"
        )

    @filter.command("抽卡分析")
    async def gacha_analysis(self, event: AstrMessageEvent):
        """生成抽卡分析图"""
        user_id = event.get_sender_id()
        binding = await self.user_mgr.get_primary_binding(user_id)
        if not binding:
            yield event.plain_result("未绑定账号。")
            return

        token = binding.get("framework_token")
        stats_data = await self.client.get_gacha_stats(token)
        if not stats_data or not stats_data.get("stats", {}).get("total_count", 0):
            yield event.plain_result(
                "暂无抽卡数据，请先发送【/抽卡分析同步】获取数据。"
            )
            return

        stats = stats_data.get("stats", {})
        analysis_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

        # Get user avatar from note API
        user_avatar = ""
        user_nickname = binding.get("nickname") or "未知"
        try:
            note_data = await self.client.get_note(
                token, binding.get("role_id"), binding.get("server_id", 1)
            )
            if note_data and "base" in note_data:
                user_avatar = note_data["base"].get("avatarUrl", "")
                user_nickname = note_data["base"].get("name", user_nickname)
        except Exception as e:
            logger.warning(f"Failed to get user avatar from note: {e}")

        # Prepare icons
        icon_map = await self._prepare_gacha_icons(token, binding)

        # Get current UP info from wiki activities
        up_info = await self._get_current_up_info()

        render_data = {
            "title": "抽卡分析",
            "subtitle": "个人数据",
            "totalCount": stats.get("total_count", 0),
            "star6": stats.get("star6_count", 0),
            "star5": stats.get("star5_count", 0),
            "star4": stats.get("star4_count", 0),
            "userNickname": user_nickname,
            "userUid": binding.get("role_id", ""),
            "userAvatar": await self.get_b64(user_avatar) if user_avatar else "",
            "analysisTime": analysis_time,
            "syncHint": "若需刷新，发送 :抽卡分析同步",
            "pluResPath": "file:///"
            + os.path.abspath(self.renderer.res_path).replace("\\", "/")
            + "/",
            "upPools": [],
            "basePools": [],
            "weaponPools": [],
            "avgUpPull": "-",
            "avgWeaponPull": "-",
            "avgBasePull": "-",
            "copyright": "Endfield Plugin | AstrBot",
            "upInfo": up_info,
        }

        up_pools, base_pools, weapon_pools = [], [], []

        # 1. Fetch limited and standard records first to calculate shared pity for limited pools
        limited_res = await self.client.get_gacha_records(
            token, pools="limited", limit=500
        )
        limited_records = limited_res.get("records", []) if limited_res else []

        # Calculate cross-banner shared pity for limited pools
        # Sort chronologically: oldest first
        limited_sorted = sorted(
            limited_records, key=lambda x: str(x.get("seq_id", "")), reverse=False
        )
        shared_pity_count = 0
        seq_id_to_pity = {}
        for r in limited_sorted:
            if not r.get("is_free"):
                shared_pity_count += 1
                seq_id_to_pity[str(r.get("seq_id", ""))] = shared_pity_count
                if int(r.get("rarity", 0)) == 6:
                    shared_pity_count = 0

        # Find the currently active limited pool (the latest one chronologically)
        active_limited_pool = None
        if limited_sorted:
            active_limited_pool = limited_sorted[-1].get("pool_name", "").strip()

        pool_types = [
            {"key": "limited", "label": "限定角色"},
            {"key": "standard", "label": "常驻角色"},
            {"key": "weapon", "label": "武器池"},
            {"key": "beginner", "label": "新手池"},
        ]

        for ptype in pool_types:
            key = ptype["key"]
            if key == "limited":
                records = limited_records
            else:
                records_res = await self.client.get_gacha_records(
                    token, pools=key, limit=500
                )
                records = records_res.get("records", []) if records_res else []

            if not records:
                continue

            # Group by pool_name
            pools_dict = {}
            for r in records:
                pname = str(r.get("pool_name", "")).strip() or "未知"
                if pname not in pools_dict:
                    pools_dict[pname] = []
                pools_dict[pname].append(r)

            for pool_name, pool_records in pools_dict.items():
                # Only show the pity bar for the active limited pool
                show_pity_bar = True
                if key == "limited" and pool_name != active_limited_pool:
                    show_pity_bar = False

                entry = await self._prepare_gacha_pool_entry(
                    pool_name,
                    pool_records,
                    key,
                    icon_map,
                    up_info,
                    seq_id_to_pity=seq_id_to_pity if key == "limited" else None,
                    show_pity_bar=show_pity_bar,
                    active_limited_pity=shared_pity_count
                    if key == "limited" and pool_name == active_limited_pool
                    else None,
                )
                if key == "weapon":
                    weapon_pools.append(entry)
                elif key == "limited":
                    up_pools.append(entry)
                else:
                    base_pools.append(entry)

        def _avg_pull(pools):
            total = sum(p.get("totalRaw", 0) for p in pools)
            star6 = sum(p.get("star6", 0) for p in pools)
            return round(total / star6) if star6 > 0 else "-"

        render_data["upPools"] = up_pools
        render_data["basePools"] = base_pools
        render_data["weaponPools"] = weapon_pools
        render_data["avgUpPull"] = _avg_pull(up_pools)
        render_data["avgWeaponPull"] = _avg_pull(weapon_pools)
        render_data["avgBasePull"] = _avg_pull(base_pools)

        try:
            url = await self.renderer.render_html(
                "gacha/gacha-analysis.html", render_data
            )
            if url:
                yield event.image_result(url)
            else:
                yield event.plain_result(
                    f"【抽卡分析】总抽数：{render_data['totalCount']}（图片渲染失败）"
                )
        except Exception as e:
            logger.error(f"Gacha analysis render failed: {e}")
            yield event.plain_result(
                f"【抽卡分析】总抽数：{render_data['totalCount']}（渲染异常）"
            )

    async def _get_current_up_info(self) -> dict:
        """Get current UP character/weapon info from wiki activities and global stats."""
        up_info = {
            "char_up_names": [],
            "weapon_up_name": "",
            "active_char_pool_name": "",
            "active_weapon_pool_name": "",
            "pool_up_map": {},  # pool_name -> up_name
        }

        # Try to get from wiki activities
        try:
            activities = await self.client.get_wiki_activities()
            if activities and isinstance(activities, list):
                # Find active limited character banner
                char_activity = next(
                    (
                        a
                        for a in activities
                        if a.get("type") == "特许寻访" and a.get("is_active")
                    ),
                    None,
                )
                if char_activity:
                    up_str = str(char_activity.get("up", "")).strip()
                    if up_str:
                        up_info["char_up_names"] = [up_str]
                    # Extract pool name from activity name (e.g., "特许寻访·热烈色彩")
                    name = char_activity.get("name", "")
                    if "·" in name:
                        up_info["active_char_pool_name"] = name.split("·", 1)[1].strip()

                # Find active weapon banner
                weapon_activity = next(
                    (
                        a
                        for a in activities
                        if a.get("type") == "武库申领" and a.get("is_active")
                    ),
                    None,
                )
                if weapon_activity:
                    up_str = str(weapon_activity.get("up", "")).strip()
                    if up_str:
                        up_info["weapon_up_name"] = up_str
                        up_info["active_weapon_pool_name"] = up_str
                    name = weapon_activity.get("name", "")
                    if "·" in name:
                        up_info["active_weapon_pool_name"] = name.split("·", 1)[
                            1
                        ].strip()

                # Build pool_up_map for all activities (including historical)
                for act in activities:
                    name = act.get("name", "")
                    up_str = str(act.get("up", "")).strip()
                    if name and up_str and "·" in name:
                        pool_name = name.split("·", 1)[1].strip()
                        up_info["pool_up_map"][pool_name] = up_str
        except Exception as e:
            logger.warning(f"Failed to get UP info from wiki activities: {e}")

        # Fallback to global stats
        if not up_info["char_up_names"]:
            try:
                global_stats = await self.client.get_gacha_global_stats()
                if global_stats and "stats" in global_stats:
                    stats = global_stats["stats"]
                    current_pool = stats.get("current_pool", {})
                    if current_pool:
                        up_chars = current_pool.get("up_char_names", [])
                        if up_chars:
                            up_info["char_up_names"] = up_chars
                        else:
                            up_char = str(current_pool.get("up_char_name", "")).strip()
                            if up_char:
                                up_info["char_up_names"] = [up_char]
                        up_weapon = str(current_pool.get("up_weapon_name", "")).strip()
                        if up_weapon:
                            up_info["weapon_up_name"] = up_weapon
                            up_info["active_weapon_pool_name"] = up_weapon

                    for p in stats.get("pool_periods", []):
                        p_name = str(p.get("pool_name", "")).strip()
                        p_ups = p.get("up_char_names", [])
                        if p_name and p_ups:
                            up_info["pool_up_map"][p_name] = p_ups[0]
                    for p in stats.get("weapon_pool_periods", []):
                        p_name = str(p.get("pool_name", "")).strip()
                        p_ups = p.get("up_weapon_names", [])
                        if p_name and p_ups:
                            up_info["pool_up_map"][p_name] = p_ups[0]
            except Exception as e:
                logger.warning(f"Failed to get UP info from global stats: {e}")

        return up_info

    def _is_up_item(
        self, name: str, pool_key: str, pool_name: str, up_info: dict
    ) -> bool:
        """Check if a 6-star item is UP based on pool type and UP info."""
        name = str(name).strip()
        if not name:
            return False

        # Check pool-specific UP map first (for historical pools)
        pool_up = None
        for p_name, u_name in up_info.get("pool_up_map", {}).items():
            if pool_name == p_name or p_name in pool_name or pool_name in p_name:
                pool_up = u_name
                break

        if pool_up:
            return name == pool_up or name in pool_up or pool_up in name

        # For limited character pool, check against current UP characters
        if pool_key == "limited":
            up_chars = up_info.get("char_up_names", [])
            for up_char in up_chars:
                if name == up_char or name in up_char or up_char in name:
                    return True

        # For weapon pool, check against current UP weapon
        if pool_key == "weapon":
            up_weapon = up_info.get("weapon_up_name", "")
            if up_weapon and (
                name == up_weapon or name in up_weapon or up_weapon in name
            ):
                return True

        return False

    async def _prepare_gacha_pool_entry(
        self,
        pool_name: str,
        records: list,
        pool_key: str,
        icon_map: dict,
        up_info: dict = None,
        seq_id_to_pity: dict = None,
        show_pity_bar: bool = True,
        active_limited_pity: int = None,
    ) -> dict:
        """Helper to process records of a specific pool into a render entry."""
        if up_info is None:
            up_info = {}
        if seq_id_to_pity is None:
            seq_id_to_pity = {}

        # Split normal and free records
        normal = [r for r in records if not r.get("is_free")]
        free = [r for r in records if r.get("is_free")]

        # Sort asc to calculate pity
        normal.sort(key=lambda x: str(x.get("seq_id", "")), reverse=False)

        images = []
        pity_count = 0
        star6_count = 0
        up_6_count = 0
        max_pity = 40 if pool_key == "weapon" else 80

        # Determine if this is a limited pool with UP
        is_limited_pool = pool_key == "limited" or (
            pool_name in up_info.get("pool_up_map", {})
        )
        # Standard and beginner pools don't have UP/wai tags
        no_wai_tag = pool_key in ["standard", "beginner"]

        # Pity calculation
        for r in normal:
            pity_count += 1
            if int(r.get("rarity", 0)) == 6:
                star6_count += 1
                name = str(r.get("char_name") or r.get("item_name", "")).strip()

                # Determine tag and badge color
                if no_wai_tag:
                    # Standard/beginner pools: no UP/wai concept
                    tag = ""
                    badge_color = "normal"
                else:
                    # Check if this is UP
                    is_up = self._is_up_item(name, pool_key, pool_name, up_info)
                    if is_up:
                        tag = "UP"
                        badge_color = "up"
                        up_6_count += 1
                    else:
                        tag = "歪"
                        badge_color = "wai"

                # Use global cross-pool pity for limited pools, if provided
                seq_id = str(r.get("seq_id", ""))
                display_pity = (
                    seq_id_to_pity.get(seq_id, pity_count)
                    if pool_key == "limited"
                    else pity_count
                )

                images.append(
                    {
                        "name": name,
                        "pullCount": display_pity,
                        "tag": tag,
                        "badgeColor": badge_color,
                        "barPercent": min(100, int((display_pity / max_pity) * 100)),
                        "barColorLevel": "green"
                        if display_pity < (max_pity * 0.6)
                        else "yellow"
                        if display_pity < (max_pity * 0.9)
                        else "red",
                        "url": await self.get_b64(icon_map.get(name, "")),
                        "fiveStars": [],
                        "refLinePercent": None,
                    }
                )
                pity_count = 0

        images.reverse()  # Newest first

        # Free pulls only show in limited pools if they actually yielded a 6 star
        # According to yunzai logic, free pills don't contribute to regular pity
        has_free_6 = False
        free_pity_count = 0
        if free:
            free.sort(key=lambda x: str(x.get("seq_id", "")), reverse=False)
            for r in free:
                free_pity_count += 1
                if int(r.get("rarity", 0)) == 6:
                    has_free_6 = True
                    name = str(r.get("char_name") or r.get("item_name", "")).strip()
                    # Only limited pools show the 6-star free pull images
                    if pool_key == "limited":
                        images.append(
                            {
                                "name": name,
                                "pullCount": free_pity_count,
                                "tag": "免费",
                                "badgeColor": "free",
                                "barPercent": min(
                                    100, int((free_pity_count / 10) * 100)
                                ),
                                "barColorLevel": "green",
                                "url": await self.get_b64(icon_map.get(name, "")),
                                "fiveStars": [],
                                "refLinePercent": None,
                            }
                        )
                    free_pity_count = 0

            # Restore free pity count to total if no 6 star was hit
            if not has_free_6:
                free_pity_count = len(free)

        # Calculate metric text and correct padding
        metric1_label = "平均花费"
        metric1_val = "-"

        total_paid_pulls = len(normal)
        total_sessions = total_paid_pulls // 10
        total_pulls = len(records)

        if pool_key == "weapon":
            # Yunzai computes weapon pool costs based on 10-pull sessions
            metric1_label = "每红花费"
            if star6_count > 0:
                metric1_val = f"{round(total_sessions / star6_count)}抽"
        else:
            if is_limited_pool:
                metric1_label = "平均UP花费"
                if up_6_count > 0:
                    metric1_val = f"{round(total_paid_pulls / up_6_count)}抽"
            else:
                metric1_label = "每红花费"
                if star6_count > 0:
                    metric1_val = f"{round(total_paid_pulls / star6_count)}抽"

        # Final display pity
        display_pity_since_last_6 = (
            active_limited_pity if active_limited_pity is not None else pity_count
        )
        if not show_pity_bar:
            display_pity_since_last_6 = None

        # UP character avatar as semi-transparent background (for limited/character pools)
        up_char_avatar_b64 = ""
        if pool_key in ("limited", "standard", "beginner"):
            up_name = up_info.get("pool_up_map", {}).get(pool_name)
            if not up_name and pool_key == "limited":
                up_names = up_info.get("char_up_names", [])
                up_name = up_names[0] if up_names else ""
            if up_name:
                avatar_url = icon_map.get(up_name, "")
                if avatar_url:
                    up_char_avatar_b64 = await self.get_b64(avatar_url) or ""

        return {
            "poolName": pool_name,
            "totalRaw": total_pulls,
            "total": f"合计 {total_pulls} 抽 - 垫 {display_pity_since_last_6}"
            if display_pity_since_last_6 and display_pity_since_last_6 > 0
            else total_pulls,
            "star6": star6_count,
            "metric1Label": metric1_label,
            "metric1": metric1_val,
            "metric2Label": "不歪率"
            if is_limited_pool and star6_count > 0
            else "出红数",
            "metric2": f"{round((up_6_count / star6_count) * 100, 1)}%"
            if is_limited_pool and star6_count > 0
            else star6_count,
            "images": images,
            "pitySinceLast6": display_pity_since_last_6,
            "pityBarPercent": min(
                100, int((display_pity_since_last_6 / max_pity) * 100)
            )
            if display_pity_since_last_6
            else 0,
            "pityBarColorLevel": "green"
            if display_pity_since_last_6
            and display_pity_since_last_6 < (max_pity * 0.6)
            else "yellow"
            if display_pity_since_last_6
            and display_pity_since_last_6 < (max_pity * 0.9)
            else "red",
            "pityFiveStars": [],
            "freeTotal": free_pity_count if pool_key == "limited" else 0,
            "inheritedPity": 0,
            "inheritedPityPercent": 0,
            "freeBarPercent": min(100, int((free_pity_count / 10) * 100))
            if pool_key == "limited" and free_pity_count > 0
            else 0,
            "upCharAvatar": up_char_avatar_b64,
        }

    @filter.command("公告")
    async def announcement_cmd(self, event: AstrMessageEvent):
        """获取公告列表 或 指定公告详情"""
        text = event.message_str.strip()
        import re

        # 1. 指定详情 (公告 <序号>)
        match_detail = re.match(r"^公告\s+(\d+)$", text)
        if match_detail:
            index = max(1, int(match_detail.group(1)))
            res = await self.client.get_announcements(1, max(index, 20))
            if not res or "list" not in res:
                yield event.plain_result("获取公告列表失败。")
                return

            list_data = res.get("list", [])
            if not list_data or index > len(list_data):
                yield event.plain_result(
                    f"找不到第 {index} 条公告（当前列表共 {len(list_data)} 条）"
                )
                return

            list_item = list_data[index - 1]
            item_id = list_item.get("item_id")
            item = list_item
            if item_id:
                detail_res = await self.client.get_announcement_detail(str(item_id))
                if detail_res:
                    item = {**list_item, **detail_res}

            render_data = build_detail_render_data(item)
            url = await self.renderer.render_html(
                "announcement/detail.html", render_data
            )
            if url:
                yield event.image_result(url)
            else:
                yield event.plain_result("渲染公告详情图片失败。")
            return

        # 2. 忽略包含多余后缀的命令（交给“公告最新”或其他逻辑处理）
        if text != "公告":
            return

        # 3. 正常获取公告列表
        res = await self.client.get_announcements(1, 5)
        if not res or "list" not in res:
            yield event.plain_result("获取公告失败。")
            return

        list_data = res.get("list", [])
        if not list_data:
            yield event.plain_result("暂无公告。")
            return

        render_data = {
            "listHeader": "终末地公告",
            "listSubtitle": f"共 {'未知' if 'total' not in res else res['total']} 条公告 (显示前 {len(list_data)} 条)",
            "list": [
                {
                    "index": i + 1,
                    "title": item.get("title") or "（未知标题）",
                    "timeStr": format_publish_time(item.get("published_at_ts")),
                    "coverUrl": get_cover_url(item),
                }
                for i, item in enumerate(list_data)
            ],
            "footerLine1": "由 AstrBot & Endfield Plugin 渲染",
            "pageWidth": 560,
        }

        url = await self.renderer.render_html("announcement/list.html", render_data)
        if url:
            yield event.image_result(url)
        else:
            yield event.plain_result("渲染公告图片失败。")

    @filter.command("公告最新")
    async def announcement_latest(self, event: AstrMessageEvent):
        """获取最新一条公告详情"""
        res = await self.client.get_announcement_latest()
        if not res:
            yield event.plain_result("获取最新公告失败。")
            return

        render_data = build_detail_render_data(res)
        url = await self.renderer.render_html("announcement/detail.html", render_data)
        if url:
            yield event.image_result(url)
        else:
            yield event.plain_result("渲染公告详情图片失败。")

    @filter.command("订阅公告")
    async def subscribe_announcement(self, event: AstrMessageEvent):
        """订阅公告推送（仅限群聊）"""
        if not event.get_group_id():
            yield event.plain_result("请在群聊中使用此命令。")
            return

        group_id = str(event.get_group_id())
        msg_origin = event.unified_msg_origin
        latest = await self.client.get_announcement_latest()
        ts = latest.get("published_at_ts", 0) if latest else 0
        await self.announce_mgr.add_subscription(group_id, ts, msg_origin)
        yield event.plain_result("已成功订阅公告推送！")

    @filter.command("取消订阅公告")
    async def unsubscribe_announcement(self, event: AstrMessageEvent):
        """取消订阅公告推送"""
        if not event.get_group_id():
            yield event.plain_result("请在群聊中使用此命令。")
            return
        group_id = event.get_group_id()
        await self.announce_mgr.remove_subscription(group_id)
        yield event.plain_result("已取消公告订阅。")

    @filter.command("订阅理智")
    async def subscribe_sanity(self, event: AstrMessageEvent):
        """订阅理智推送（满时提醒，覆盖旧订阅）"""
        user_id = event.get_sender_id()
        binding = await self.user_mgr.get_primary_binding(user_id)
        if not binding:
            yield event.plain_result("请先绑定森空岛账号后再订阅理智提醒。")
            return

        msg_origin = event.unified_msg_origin
        await self.sanity_mgr.add_subscription(str(user_id), msg_origin)
        yield event.plain_result("已成功订阅理智满时提醒！将在本会话推送通知。")

    @filter.command("取消订阅理智")
    async def unsubscribe_sanity(self, event: AstrMessageEvent):
        """取消理智推送"""
        user_id = event.get_sender_id()
        success = await self.sanity_mgr.remove_subscription(str(user_id))
        if success:
            yield event.plain_result("已取消理智订阅。")
        else:
            yield event.plain_result("您当前没有订阅过理智提醒。")

    @filter.command("日历")
    async def calendar_cmd(self, event: AstrMessageEvent):
        """活动日历图"""
        res = await self.client.get_wiki_activities()

        # If res is None, the request failed (e.g., 401 Unauthorized or network error)
        if res is None:
            yield event.plain_result(
                "获取活动列表失败，无法连接到终末地维基。请检查您的 API Key 是否已正确配置。"
            )
            return

        # Ensure res is a list
        if not isinstance(res, list):
            if hasattr(res, "get"):
                if "activities" in res:
                    res = res.get("activities", [])
                elif "data" in res:
                    res = res.get("data", [])
                elif "list" in res:
                    res = res.get("list", [])
                else:
                    res = []
            else:
                res = []

        if not res:
            yield event.plain_result(
                "获取到了活动列表，但当前列表为空（服务器暂无活动信息）。"
            )
            return

        # Build data for rendering
        import datetime

        now = datetime.datetime.now()
        now_ts = int(now.timestamp())

        normal_acts = []
        perm_acts = []
        min_ts = float("inf")

        for act in res:
            try:
                st_ts = (
                    act.get("activity_start_at_ts")
                    or act.get("activityStartAtTs")
                    or act.get("start_at_ts")
                )
                et_ts = (
                    act.get("activity_end_at_ts")
                    or act.get("activityEndAtTs")
                    or act.get("end_at_ts")
                )

                if not st_ts or not et_ts:
                    continue

                st_ts = int(st_ts)
                et_ts = int(et_ts)

                duration_days = (et_ts - st_ts) / 86400
                st = datetime.datetime.fromtimestamp(st_ts)
                et = datetime.datetime.fromtimestamp(et_ts)

                is_active = st_ts <= now_ts <= et_ts

                desc = act.get("description", "活动")
                is_perm = duration_days >= 300
                if is_perm and desc in ["", "玩法说明", "新手活动"]:
                    desc = "常驻活动"

                parsed_act = {
                    "name": act.get("name", "未知活动"),
                    "desc": desc,
                    "start": st.strftime("%m.%d"),
                    "end": et.strftime("%m.%d"),
                    "st_ts": st_ts,
                    "et_ts": et_ts,
                    "is_active": is_active,
                    "cover": act.get("pic", ""),
                    "is_perm": is_perm,
                }

                dt_start = datetime.datetime.fromtimestamp(parsed_act["st_ts"])
                dt_end = datetime.datetime.fromtimestamp(parsed_act["et_ts"])

                # Check permanent: duration >= 300 days (~1 year)
                is_perm = False
                if (parsed_act["et_ts"] - parsed_act["st_ts"]) >= 300 * 24 * 3600:
                    is_perm = True

                parsed_act["start"] = dt_start.strftime("%m.%d %H:%M")
                parsed_act["end"] = dt_end.strftime("%m.%d %H:%M")
                parsed_act["is_perm"] = is_perm

                # Fetches the long banner using Wiki API, or falls back to 'pic' sticker
                parsed_act["cover"] = await self.get_activity_banner(act)

                if parsed_act["is_perm"]:
                    perm_acts.append(parsed_act)
                else:
                    normal_acts.append(parsed_act)
                    # min_ts = min(min_ts, st_ts) # Removed as we use fixed window now

            except Exception as e:
                logger.error(f"Error parsing activity time: {e}")

        if not normal_acts and not perm_acts:
            yield event.plain_result("暂无可解析的活动事件。")
            return

        # ─── Fixed Window: today -10 days … today +50 days ────────
        import datetime as _dt

        today_midnight = _dt.datetime.combine(now.date(), _dt.time.min)
        min_ts = int(today_midnight.timestamp()) - 10 * 86400
        max_ts = int(today_midnight.timestamp()) + 50 * 86400
        total_duration = 60 * 86400  # 60 days total window

        # Select key dates for axis
        key_dates = set()

        # Combine acts: normal events first, permanent events at the back
        normal_acts.sort(key=lambda x: x["st_ts"])
        perm_acts.sort(key=lambda x: x["st_ts"])
        all_acts = normal_acts + perm_acts

        for act in all_acts:
            left_pct = (act["st_ts"] - min_ts) / total_duration * 100
            right_pct = (act["et_ts"] - min_ts) / total_duration * 100

            if act["is_perm"]:
                right_pct = 100

            left_pct = max(0, min(100, left_pct))
            right_pct = max(0, min(100, right_pct))

            width_pct = right_pct - left_pct

            # Minimum width based on pixel count (200px out of 3000px ≈ 6.67%)
            if width_pct < 6.67:
                width_pct = 6.67
                if left_pct + width_pct > 100:
                    left_pct = 100 - width_pct

            act["left_pct"] = left_pct
            act["width_pct"] = width_pct

            # Hide start time if it's outside our left boundary
            act["hide_start"] = act["st_ts"] < min_ts

            if 0 <= left_pct <= 100 and not act["is_perm"]:
                key_dates.add(act["st_ts"])

        # Pack into lanes
        lanes = []
        for act in normal_acts:
            placed = False
            for lane in lanes:
                last_act = lane[-1]
                # Assuming 1 day (86400s) padding minimum between events in the same lane
                if act["st_ts"] >= last_act["et_ts"] + 86400:
                    lane.append(act)
                    placed = True
                    break
            if not placed:
                lanes.append([act])

        # Permanent acts go to the bottom, they should not mix with normal_acts lanes
        perm_lanes = []
        for act in perm_acts:
            placed = False
            for lane in perm_lanes:
                last_act = lane[-1]
                if act["st_ts"] >= last_act["et_ts"] + 86400:
                    lane.append(act)
                    placed = True
                    break
            if not placed:
                perm_lanes.append([act])

        # Append perm lanes to normal lanes so they render at the bottom
        lanes.extend(perm_lanes)

        axis_dates = []
        last_ts = 0
        min_p_gap = 4 * 86400  # 至少间隔4天，防止日期标签重叠
        for ds in sorted(list(key_dates)):
            if ds - last_ts < min_p_gap:
                continue
            last_ts = ds
            dt = datetime.datetime.fromtimestamp(ds)
            axis_dates.append(
                {
                    "label": dt.strftime("%m.%d"),
                    "left_pct": (ds - min_ts) / total_duration * 100,
                }
            )

        # Today's line
        now_pct = (now_ts - min_ts) / total_duration * 100
        now_line = None
        if 0 <= now_pct <= 100:
            now_line = {"label": "TODAY", "left_pct": now_pct}

        render_data = {
            "pluResPath": "file:///"
            + os.path.abspath(self.renderer.res_path).replace("\\", "/")
            + "/",
            "title": "版本日历",
            "lanes": lanes,
            "axis_dates": axis_dates,
            "now_line": now_line,
            "copyright": "AstrBot & Endfield Plugin",
            "pageWidth": 3000,
        }

        url = await self.renderer.render_html("calendar/calendar.html", render_data)
        if url:
            yield event.image_result(url)
        else:
            yield event.plain_result("活动日历渲染失败。")

    async def announcement_task(self):
        """后台公告推送任务"""
        logger.info("[公告推送] 任务已启动")
        while True:
            try:
                subs = await self.announce_mgr.get_subscriptions()
                if not subs:
                    # Wait if no subscriptions
                    await asyncio.sleep(600)
                    continue

                latest = await self.client.get_announcement_latest()
                if not latest or "published_at_ts" not in latest:
                    await asyncio.sleep(600)
                    continue

                ts = int(latest["published_at_ts"])
                logger.debug(f"[公告推送] 轮询完成，最新公告 TS: {ts}")

                for s in subs:
                    since_ts = int(s.get("since_ts", 0))
                    if ts > since_ts:
                        logger.info(
                            f"[公告推送] 发现新公告: {latest.get('title')}，正在推送给 {s.get('group_id')}..."
                        )
                        item_id = latest.get("item_id")
                        item = latest
                        if item_id:
                            detail_res = await self.client.get_announcement_detail(
                                str(item_id)
                            )
                            if detail_res:
                                item = {**latest, **detail_res}

                        render_data = build_detail_render_data(item)
                        url = await self.renderer.render_html(
                            "announcement/detail.html", render_data
                        )
                        msg_origin = s.get("msg_origin", "")
                        if not msg_origin:
                            logger.warning(
                                f"[公告订阅] 群 {s.get('group_id')} 缺少 msg_origin，请尝试重新订阅"
                            )
                            continue
                        try:
                            chain = MessageChain()
                            if url:
                                # Standardize path for Windows
                                img_path = os.path.normpath(url.replace("file:///", ""))
                                chain.chain.append(Image.fromFileSystem(img_path))
                            else:
                                msg = f"【终末地新公告】\n{latest.get('title')}\n{latest.get('summary') or ''}"
                                chain.chain.append(Plain(msg))

                            await self.context.send_message(msg_origin, chain)
                            logger.info(f"[公告推送] 公告推送成功: {s.get('group_id')}")
                        except Exception as e:
                            logger.error(
                                f"[公告推送] 推送失败 (target={msg_origin}): {e}"
                            )
                        await self.announce_mgr.update_since_ts(s["group_id"], ts)

                poll_interval_mins = int(
                    self.config.get("announcement_poll_interval", 10)
                )
                await asyncio.sleep(max(60, poll_interval_mins * 60))
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[公告推送] 任务进程异常: {e}")
                await asyncio.sleep(60)

    async def sanity_task(self):
        """理智满值通知推送任务（可配置轮询间隔）"""
        logger.info("[理智推送] 任务已启动")
        while True:
            try:
                subs = await self.sanity_mgr.get_subscriptions()
                if not subs:
                    await asyncio.sleep(600)
                    continue

                now_ts = int(time.time())
                logger.debug(f"[理智推送] 开始扫描 {len(subs)} 个订阅")

                for sub in subs:
                    user_id = sub.get("user_id")
                    msg_origin = sub.get("msg_origin", "")
                    last_notified = sub.get("last_notified", 0)

                    if not msg_origin:
                        logger.warning(
                            f"[理智订阅] 用户 {user_id} 缺少 msg_origin，请重新订阅"
                        )
                        continue

                    # Cooldown check (4h default)
                    if now_ts - last_notified < 3600 * 4:
                        continue

                    binding = await self.user_mgr.get_primary_binding(user_id)
                    if not binding:
                        logger.debug(f"[理智推送] 用户 {user_id} 未找到绑定，跳过")
                        continue

                    token = binding.get("framework_token")
                    role_id = binding.get("role_id")
                    server_id = binding.get("server_id", 1)

                    if not token or not role_id:
                        logger.debug(
                            f"[理智推送] 用户 {user_id} 缺少 Token 或 RoleID，跳过"
                        )
                        continue

                    try:
                        stamina_data = await self.client.get_stamina(
                            token, role_id, server_id
                        )
                        if not stamina_data:
                            logger.warning(
                                f"[理智推送] 无法获取用户 {user_id} 的理智数据"
                            )
                            continue

                        stamina_obj = stamina_data.get("stamina", {})
                        s_current = int(stamina_obj.get("current", 0) or 0)
                        s_max = int(stamina_obj.get("max", 0) or 0)

                        logger.debug(f"[理智推送] 用户 {user_id}: {s_current}/{s_max}")

                        if s_current > 0 and s_max > 0 and s_current >= s_max:
                            if last_notified != 0:
                                logger.debug(
                                    f"[理智推送] 用户 {user_id} 理智已满但已提醒过，跳过"
                                )
                                continue

                            try:
                                nick = binding.get("nickname") or "干员"
                                msg = f"【理智已满】{nick}，您的理智已达到上限（{s_current}/{s_max}），请及时消耗。"
                                logger.info(
                                    f"[理智推送] 发现理智已满，正在推送给用户 {user_id}..."
                                )

                                chain = MessageChain()
                                chain.chain.append(At(qq=user_id))
                                chain.chain.append(Plain(f"\n{msg}"))

                                await self.context.send_message(msg_origin, chain)
                                await self.sanity_mgr.update_last_notified(
                                    user_id, 1
                                )  # Set to non-zero to mark as notified
                                logger.info(f"[理智推送] 推送成功: {user_id}")
                            except Exception as e:
                                logger.error(
                                    f"[理智推送] 推送失败 (target={msg_origin}): {e}"
                                )
                        else:
                            # Sanity not full, reset notification flag if it was set
                            if last_notified != 0:
                                logger.info(
                                    f"[理智推送] 用户 {user_id} 理智未满 ({s_current}/{s_max})，重置提醒标志"
                                )
                                await self.sanity_mgr.update_last_notified(user_id, 0)
                    except Exception as e:
                        logger.error(f"[理智推送] 数据拉取异常 (user={user_id}): {e}")

                    await asyncio.sleep(1.5)

                poll_interval_mins = int(self.config.get("sanity_poll_interval", 20))
                await asyncio.sleep(max(60, poll_interval_mins * 60))
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[理智推送] 任务异常: {e}")
                await asyncio.sleep(60)

    async def auto_sign_in_task(self):
        """每日自动签到任务"""
        import datetime

        while True:
            try:
                # 计算下次签到的等待时间
                now = datetime.datetime.now()
                target_time_str = self.auto_sign_in_time
                try:
                    target_hour, target_minute = map(int, target_time_str.split(":"))
                except ValueError:
                    target_hour, target_minute = 0, 5  # 如果格式非法，默认 00:05

                target_time = now.replace(
                    hour=target_hour, minute=target_minute, second=0, microsecond=0
                )
                if now > target_time:
                    # 如果今天的目标时间已过，则安排在明天
                    target_time += datetime.timedelta(days=1)

                wait_seconds = (target_time - now).total_seconds()
                logger.info(
                    f"[Endfield Auto Sign-In] Next auto sign-in scheduled at {target_time.strftime('%Y-%m-%d %H:%M:%S')} (in {wait_seconds:.1f} seconds)"
                )

                # 睡眠直到目标时间
                await asyncio.sleep(wait_seconds)

                # 执行批量签到
                await self.run_batch_sign_in()

            except asyncio.CancelledError:
                logger.info("[Endfield Auto Sign-In] Task cancelled.")
                break
            except Exception as e:
                logger.error(
                    f"[Endfield Auto Sign-In] Unexpected error in task loop: {e}"
                )
                await asyncio.sleep(60)  # 出错后延迟重试，防止死循环

    async def run_batch_sign_in(self):
        """执行所有账号的自动签到"""
        import datetime

        logger.info("[Endfield Auto Sign-In] Starting batch sign-in...")

        all_bindings = await self.user_mgr.get_all_bindings()
        user_ids_seen = set()
        account_count = 0
        success_count = 0
        fail_count = 0

        for bind in all_bindings:
            token = bind.get("framework_token")
            role_id = bind.get("role_id")
            user_id = bind.get("_user_id")
            if not token or not role_id:
                continue

            user_ids_seen.add(user_id)
            account_count += 1

            try:
                res = await self.client.get_attendance(token)
                # 签到成功或重复签到
                if res and isinstance(res, dict):
                    success_count += 1
                    logger.info(f"[Endfield Auto Sign-In] Success for role {role_id}")
                else:
                    fail_count += 1
                    logger.warning(
                        f"[Endfield Auto Sign-In] Failed/Error for role {role_id}: {res}"
                    )
            except Exception as e:
                fail_count += 1
                logger.error(
                    f"[Endfield Auto Sign-In] Exception for role {role_id}: {e}"
                )

            # 使用可配置的间隔
            await asyncio.sleep(max(0.1, self.auto_sign_in_interval))

        logger.info(
            f"[Endfield Auto Sign-In] Batch complete. Users: {len(user_ids_seen)}, Accounts: {account_count}, Success: {success_count}, Failed: {fail_count}"
        )

        # Update last sign date
        now_date = datetime.datetime.now().strftime("%Y-%m-%d")
        await self.sign_mgr.set_last_sign_date(now_date)

        # 推送通知汇总
        if self.auto_sign_in_notify_group:
            target = self.auto_sign_in_notify_group
            # 如果不是统一 ID（不含 :），尝试通过现有订阅推断平台信息
            if ":" not in target:
                found_origin = ""
                # 优先尝试公告订阅
                ann_subs = await self.announce_mgr.get_subscriptions()
                for s in ann_subs:
                    if s.get("msg_origin") and ":" in s["msg_origin"]:
                        parts = s["msg_origin"].split(":")
                        if len(parts) >= 3:
                            # 使用相同的平台和类型（通常是群聊）
                            found_origin = f"{parts[0]}:{parts[1]}:{target}"
                            break
                if not found_origin:
                    # 尝试理智订阅
                    san_subs = await self.sanity_mgr.get_subscriptions()
                    for s in san_subs:
                        if s.get("msg_origin") and ":" in s["msg_origin"]:
                            parts = s["msg_origin"].split(":")
                            if len(parts) >= 3:
                                found_origin = f"{parts[0]}:{parts[1]}:{target}"
                                break
                if found_origin:
                    target = found_origin
                    logger.info(
                        f"[Endfield Auto Sign-In] Resolved plain ID to unified ID: {target}"
                    )

            msg = (
                "森空岛自动签到已执行\n"
                f"用户数：{len(user_ids_seen)}\n"
                f"账号数：{account_count}\n"
                f"成功数：{success_count}\n"
                f"失败数：{fail_count}"
            )
            try:
                await self.context.send_message(target, MessageChain([Plain(msg)]))
            except Exception as e:
                logger.error(
                    f"[Endfield Auto Sign-In] Failed to send notification (target={target}): {e}"
                )
                if ":" not in str(target):
                    logger.warning(
                        "[Endfield Auto Sign-In] Tip: Please provide the full Unified ID in settings, e.g., 'aiocqhttp:group:123456'"
                    )

    @filter.command("帝江号建设", alias=["帝江号"])
    async def spaceship_cmd(self, event: AstrMessageEvent):
        """查询帝江号建设信息"""
        user_id = event.get_sender_id()
        bind = await self.user_mgr.get_primary_binding(user_id)
        if not bind:
            yield event.plain_result("您尚未绑定森空岛账号，请先使用 /授权登陆 绑定。")
            return

        res = await self.client.get_spaceship(
            bind["framework_token"], bind["role_id"], int(bind.get("server_id", 1))
        )
        note = await self.client.get_note(
            bind["framework_token"], bind["role_id"], int(bind.get("server_id", 1))
        )

        # 从便签接口构建干员头像映射（id → avatarSqUrl）
        char_avatar_map = {}
        user_avatar = ""
        if note:
            user_avatar = note.get("base", {}).get("avatarUrl", "")
            for c in note.get("chars", []):
                char_avatar_map[c.get("id")] = c.get("avatarSqUrl")

        if not res:
            yield event.plain_result("获取帝江号建设信息失败。")
            return

        # API (4): 顶层 rooms 含 roomName/moodPercent/trustPercent/trustLevelName 等计算好的字段
        # spaceShip.rooms 是旧向下兼容字段，无 roomName，不再使用
        rooms_raw = res.get("rooms", []) if isinstance(res, dict) else []
        char_name_map = res.get("charNameMap", {}) if isinstance(res, dict) else {}
        role_info = res.get("role", {}) if isinstance(res, dict) else {}

        # Build char avatar map from note chars: charId -> avatarSqUrl, name -> avatarSqUrl
        name_to_avatar = (
            {c.get("name"): c.get("avatarSqUrl") for c in note.get("chars", [])}
            if note
            else {}
        )

        render_data = {
            "title": "帝江号建设汇报",
            "userNickname": (note or {}).get("base", {}).get("name")
            or role_info.get("name")
            or bind.get("nickname", "未知"),
            "userLevel": (note or {}).get("base", {}).get("level", bind.get("level", 0))
            if note
            else role_info.get("level", bind.get("level", 0)),
            "userUid": (note or {}).get("base", {}).get("roleId")
            or role_info.get("roleId")
            or bind.get("role_id", "0"),
            "userAvatar": user_avatar or role_info.get("avatarUrl", ""),
            "roomCount": len(rooms_raw),
            "rooms": [],
            "pluResPath": "file:///"
            + os.path.abspath(self.renderer.res_path).replace("\\", "/")
            + "/",
        }

        for idx, room in enumerate(rooms_raw):
            room_data = {
                "roomName": room.get("roomName") or room.get("id") or "未知房间",
                "level": room.get("level", 1),
                "bgIndex": (idx % 3) + 1,
                "chars": [],
            }
            for c in room.get("chars", []):
                char_id = c.get("charId") or ""
                # charNameMap maps UUID→Chinese name; c.get("name") may be internal ID like chr_0021_whiten
                # Prefer charNameMap, then check if c.name looks like a real name (has CJK chars)
                name_from_map = char_name_map.get(char_id, "")
                name_raw = c.get("name", "")
                name_is_real = name_raw and any("\u4e00" <= ch <= "\u9fff" for ch in name_raw)
                char_name = name_from_map or (name_raw if name_is_real else "") or "未知"
                avatar_url = c.get("avatarUrl") or char_avatar_map.get(char_id) or name_to_avatar.get(char_name, "")
                mood_pct = c.get("moodPercent", 0)
                trust_pct = c.get("trustPercent", 0)
                room_data["chars"].append(
                    {
                        "name": char_name,
                        "avatar": avatar_url,
                        "moodDisplay": c.get("moodDisplay") or f"{mood_pct}/100",
                        "trustLevelName": c.get("trustLevelName", ""),
                        "trustPercent": trust_pct,
                        "trustDisplay": c.get("trustDisplay") or f"{trust_pct}%",
                    }
                )
            render_data["rooms"].append(room_data)

        render_data["copyright"] = "astrbot"

        url = await self.renderer.render_html("area/spaceship.html", render_data)
        if url:
            yield event.image_result(url)
        else:
            yield event.plain_result("渲染帝江号建设图片失败。")

    @filter.command("地区建设", alias=["建设"])
    async def area_cmd(self, event: AstrMessageEvent):
        """查询地区建设信息"""
        user_id = event.get_sender_id()
        bind = await self.user_mgr.get_primary_binding(user_id)
        if not bind:
            yield event.plain_result("您尚未绑定森空岛账号，请先使用 /授权登录 绑定。")
            return

        res = await self.client.get_domain(
            bind["framework_token"], bind["role_id"], int(bind.get("server_id", 1))
        )
        note = await self.client.get_note(
            bind["framework_token"], bind["role_id"], int(bind.get("server_id", 1))
        )

        char_avatar_map = {}
        user_avatar = ""
        if note:
            user_avatar = note.get("base", {}).get("avatarUrl", "")
            for c in note.get("chars", []):
                char_avatar_map[c.get("id")] = c.get("avatarSqUrl")

        if not res:
            yield event.plain_result("获取地区建设信息失败。")
            return

        # Data transformation for area.html
        render_data = {
            "title": "地区建设进度",
            "userNickname": bind.get("nickname", "未知"),
            "userLevel": note.get("base", {}).get("level", bind.get("level", 0))
            if note
            else bind.get("level", 0),
            "userUid": bind.get("role_id", "0"),
            "userAvatar": user_avatar,
            "zoneCount": len(res.get("domain", [])),
            "zones": [],
            "pluResPath": "file:///"
            + os.path.abspath(self.renderer.res_path).replace("\\", "/")
            + "/",
        }

        # Build fallback maps from note (often more reliable for names/avatars)
        note_chars = note.get("chars", []) if note else []
        note_name_map = {c.get("id"): c.get("name") for c in note_chars}
        note_avatar_map = {c.get("id"): c.get("avatarSqUrl") for c in note_chars}

        # Merge with domain API's own map (if provided)
        # Using a unified map ensures we can resolve both short IDs (char_001) and long hashes
        char_name_map = {**note_name_map, **res.get("charNameMap", {})}
        char_avatar_map = note_avatar_map  # note is the primary source for avatars

        # Build a name-to-avatar map as a last resort fallback
        name_to_avatar = {c.get("name"): c.get("avatarSqUrl") for c in note_chars}

        for domain in res.get("domain", []):
            zone_data = {
                "zoneName": domain.get("name", "未知地区"),
                "level": domain.get("level", 1),
                "totalChest": sum(
                    c.get("trchestCount", 0) for c in domain.get("collections", [])
                ),
                "totalPuzzle": sum(
                    c.get("puzzleCount", 0) for c in domain.get("collections", [])
                ),
                "totalBlackbox": sum(
                    c.get("blackboxCount", 0) for c in domain.get("collections", [])
                ),
                "settlements": [],
            }
            for s in domain.get("settlements", []):
                # Try multiple possible field names for flexibility
                officer_ids = (
                    s.get("officerCharIds")
                    or s.get("officerCharId")
                    or s.get("officers")
                    or ""
                )

                if isinstance(officer_ids, list) and officer_ids:
                    officer_id = str(officer_ids[0])
                else:
                    officer_id = str(officer_ids) if officer_ids else ""

                # Resolve name and avatar with fallbacks
                officer_name = char_name_map.get(officer_id, "")
                officer_avatar = char_avatar_map.get(officer_id) or name_to_avatar.get(
                    officer_name, ""
                )

                # Ticket data
                rem_m = s.get("remainMoney", 0)
                max_m = s.get("moneyMax", 1)  # Avoid div by zero
                try:
                    rem_m_f = float(rem_m)
                    max_m_f = float(max_m)
                except (ValueError, TypeError):
                    rem_m_f = 0.0
                    max_m_f = 1.0

                m_percent = min(100, round(rem_m_f / max_m_f * 100))

                zone_data["settlements"].append(
                    {
                        "name": s.get("name", "未知聚落"),
                        "level": s.get("level", 1),
                        "officerName": officer_name,
                        "officerAvatar": officer_avatar,
                        "remainMoney": rem_m,
                        "moneyMax": max_m,
                        "moneyPercent": m_percent,
                        "isFull": rem_m_f >= max_m_f and max_m_f > 0,
                    }
                )
            render_data["zones"].append(zone_data)

        render_data["copyright"] = "astrbot"

        url = await self.renderer.render_html("area/area.html", render_data)
        if url:
            yield event.image_result(url)
        else:
            yield event.plain_result("渲染地区建设图片失败。")

    def _prepare_operator_render_data(
        self, full_res: dict, panel_stats: dict, binding: dict, matched: dict
    ) -> dict:
        """将原始 API 数据解析为模板友好的渲染数据。"""
        detail = (
            (full_res.get("data") or {}).get("detail")
            or full_res.get("detail")
            or full_res
        )
        char_data = detail.get("charData") or {}
        user_skills = detail.get("userSkills") or {}

        def _parse_rarity(raw):
            val = raw.get("value", 1) if isinstance(raw, dict) else raw
            try:
                return max(1, min(6, int(val)))
            except:
                return 1

        rarity = _parse_rarity(char_data.get("rarity", {}))
        potential_level = min(5, max(0, int(detail.get("potentialLevel", 0) or 0)))

        def _get_val(obj, key="name", default=""):
            if not obj:
                return default
            if isinstance(obj, dict):
                return obj.get(key) or default
            return str(obj)

        _skill_type_map = {
            "skill_type_normal_attack": "普通攻击",
            "skill_type_normal_skill": "战技",
            "skill_type_combo_skill": "连携技",
            "skill_type_ultimate_skill": "终结技",
        }

        skills = []
        for s in char_data.get("skills") or []:
            if not isinstance(s, dict):
                continue
            u = (
                (user_skills.get(s.get("id", "")) or {})
                if isinstance(user_skills, dict)
                else {}
            )
            sk_type = s.get("type")
            sk_key = (
                sk_type.get("key", "")
                if isinstance(sk_type, dict)
                else str(sk_type or "")
            )
            skills.append(
                {
                    "name": s.get("name", "未知"),
                    "iconUrl": s.get("iconUrl", ""),
                    "level": u.get("level", 1) if isinstance(u, dict) else 1,
                    "maxLevel": u.get("maxLevel", "") if isinstance(u, dict) else "",
                    "typeLabel": _skill_type_map.get(sk_key, "") or _get_val(sk_type),
                    "typeKey": sk_key,
                }
            )
        while len(skills) < 4:
            skills.append({"empty": True})

        def _pick_equip(raw):
            if not raw or not isinstance(raw, dict):
                return None
            # e 是核心数据对象（名称、图标等）
            e = (
                raw.get("equipData")
                or raw.get("weaponData")
                or raw.get("tacticalItemData")
                or raw
            )

            if not e or not isinstance(e, dict) or not e.get("name"):
                return None

            r = _parse_rarity(e.get("rarity", {}))
            # 等级通常在 raw 中（武器/装备），或在某些结构的 e 中
            lv_raw = raw.get("level") if "level" in raw else e.get("level")
            lv = lv_raw.get("value") if isinstance(lv_raw, dict) else lv_raw

            # 套装信息可能在 raw 或 e 中
            suit_data = (
                raw.get("equipSuitData")
                or raw.get("suit")
                or e.get("equipSuitData")
                or e.get("suit")
            )
            suit_name = ""
            if isinstance(suit_data, dict):
                suit_name = suit_data.get("name", "")

            # 武器新增字段：突破等级、精炼等级
            breakthrough = raw.get("breakthroughLevel")
            refine = raw.get("refineLevel")
            if refine is not None:
                refine += 1  # 从 0 索引转换为 1 索引

            # 新增宝石字段
            gem_raw = raw.get("gem") or e.get("gem")
            gem = None

            def _gem_rarity_num(gd):
                """Extract numeric rarity from gemData dict."""
                import re as _re
                # Try rarity field first (may be dict or int/str)
                r = gd.get("rarity")
                if r is not None:
                    num = _parse_rarity(r)
                    if num > 1:
                        return num
                # Fall back to templateId like "ITEM_GEM_RARITY_5"
                tid = str(gd.get("templateId", ""))
                m = _re.search(r"(\d+)$", tid)
                if m:
                    return int(m.group(1))
                return 0

            def _parse_skill_values(skills_list):
                """Extract display values from [{key, value}] skill list."""
                if not isinstance(skills_list, list):
                    return []
                return [
                    s["value"] for s in skills_list
                    if isinstance(s, dict) and s.get("value")
                ]

            if isinstance(gem_raw, dict) and gem_raw.get("gemData"):
                gd = gem_raw["gemData"]
                rn = _gem_rarity_num(gd)
                gem = {
                    "name": gd.get("name", ""),
                    "icon": gd.get("iconUrl", "") or gd.get("icon", ""),
                    "rarity": rn,
                    "stars": list(range(1, rn + 1)) if rn else [],
                    "level": gem_raw.get("level"),
                    "gemSkills": _parse_skill_values(gd.get("skills", [])),
                }
            elif isinstance(gem_raw, dict) and gem_raw.get("name"):
                rn = _gem_rarity_num(gem_raw)
                gem = {
                    "name": gem_raw.get("name", ""),
                    "icon": gem_raw.get("iconUrl", "") or gem_raw.get("icon", ""),
                    "rarity": rn,
                    "stars": list(range(1, rn + 1)) if rn else [],
                    "level": gem_raw.get("level"),
                    "gemSkills": _parse_skill_values(gem_raw.get("skills", [])),
                }

            passive_skills = []
            raw_skills = e.get("skills")  # 通常在 weaponData (e) 中
            if isinstance(raw_skills, list):
                passive_skills = [
                    s["value"] for s in raw_skills
                    if isinstance(s, dict) and s.get("value")
                ]

            return {
                "name": e.get("name", ""),
                "iconUrl": e.get("iconUrl", ""),
                "level": lv or 1,
                "stars": list(range(1, r + 1)),
                "suitName": suit_name,
                "suitCount": 1,
                "breakthroughLevel": breakthrough,
                "refineLevel": refine,
                "passiveSkills": passive_skills,
                "gem": gem,
            }

        weapon = _pick_equip(detail.get("weapon"))
        body_equip = _pick_equip(detail.get("bodyEquip"))
        arm_equip = _pick_equip(detail.get("armEquip"))
        first_accessory = _pick_equip(detail.get("firstAccessory"))
        second_accessory = _pick_equip(detail.get("secondAccessory"))

        # 统计所有护甲和配件的套装件数
        equips = [body_equip, arm_equip, first_accessory, second_accessory]
        suit_counts = {}
        for e in equips:
            if e and e.get("suitName"):
                sn = e["suitName"]
                suit_counts[sn] = suit_counts.get(sn, 0) + 1
        for e in equips:
            if e and e.get("suitName"):
                e["suitCount"] = suit_counts[e["suitName"]]

        tactical_raw = detail.get("tacticalItem")
        tactical_item = None
        if tactical_raw and isinstance(tactical_raw, dict):
            t = tactical_raw.get("tacticalItemData") or tactical_raw
            if isinstance(t, dict) and t.get("name"):
                tactical_item = {
                    "name": t.get("name", ""),
                    "iconUrl": t.get("iconUrl", ""),
                    "activeEffect": t.get("activeEffect", ""),
                }

        # Handle illustration: fallback across multiple common keys
        illustration_url = (
            char_data.get("illustrationUrl")
            or char_data.get("fullAvatarUrl")
            or matched.get("illustrationUrl")
            or matched.get("fullAvatarUrl")
            or ""
        )

        tags_list = []
        for t in char_data.get("tags") or []:
            if isinstance(t, dict):
                n = t.get("name")
                if n:
                    tags_list.append(n)
            elif isinstance(t, str):
                tags_list.append(t)

        # Talents integration
        talents = []
        for t_list_key in ["abilityTalents", "combatTalents", "cultivationTalents"]:
            t_list = matched.get(t_list_key) or []
            for t in t_list:
                if isinstance(t, dict) and t.get("name"):
                    talents.append(
                        {
                            "name": t.get("name"),
                            "typename": "天赋"
                            if t_list_key == "abilityTalents"
                            else ("战斗" if t_list_key == "combatTalents" else "整备"),
                            "iconUrl": t.get("iconUrl", ""),
                        }
                    )

        # Breakthrough (Talent nodes)
        import re as _re

        # Build talent id/name lookup for display
        _talent_id_name: dict = {}
        for _tk in ["abilityTalents", "combatTalents", "cultivationTalents"]:
            for _t in (matched.get(_tk) or []):
                if isinstance(_t, dict):
                    _tid = str(_t.get("id", ""))
                    _tname = str(_t.get("name", ""))
                    if _tid and _tname:
                        _talent_id_name[_tid] = _tname

        def _node_display(node_id: str) -> str:
            """Convert raw talent node ID to readable display text."""
            s = str(node_id)
            # Exact match
            if s in _talent_id_name:
                return _talent_id_name[s]
            # Prefix match: strip trailing _N level suffix, look up base name
            parts = s.rsplit("_", 1)
            if len(parts) == 2 and parts[1].isdigit():
                prefix = parts[0]
                level = parts[1]
                if prefix in _talent_id_name:
                    return f"{_talent_id_name[prefix]} Lv.{level}"
                # Try one more level of suffix stripping
                parts2 = prefix.rsplit("_", 1)
                if len(parts2) == 2 and parts2[1].isdigit():
                    prefix2 = parts2[0]
                    if prefix2 in _talent_id_name:
                        return f"{_talent_id_name[prefix2]} Lv.{level}"
            # charBreak{N} → just show Lv.N
            m = _re.match(r"^charBreak(\d+)$", s)
            if m:
                return f"Lv.{m.group(1)}"
            # Generic: extract trailing digit as level
            m = _re.search(r"_(\d+)$", s)
            if m:
                return f"Lv.{m.group(1)}"
            return s

        talent_nodes = []
        raw_talent = matched.get("talent") or {}
        if isinstance(raw_talent, dict):
            if raw_talent.get("latestBreakNode"):
                talent_nodes.append(
                    {"label": "突破", "value": _node_display(raw_talent["latestBreakNode"])}
                )
            if raw_talent.get("latestPassiveSkillNodes"):
                for n in raw_talent["latestPassiveSkillNodes"]:
                    talent_nodes.append({"label": "被动", "value": _node_display(n)})
            if raw_talent.get("latestFactorySkillNodes"):
                for n in raw_talent["latestFactorySkillNodes"]:
                    talent_nodes.append({"label": "工厂", "value": _node_display(n)})
            if raw_talent.get("latestSpaceshipSkillNodes"):
                for n in raw_talent["latestSpaceshipSkillNodes"]:
                    talent_nodes.append({"label": "帝江", "value": _node_display(n)})

        return {
            "name": char_data.get("name", "未知"),
            "level": detail.get("level", 1),
            "rarity": rarity,
            "stars": list(range(1, rarity + 1)),
            "potentialLevel": potential_level,
            "potentialStars": [
                {"active": i < potential_level, "index": i + 1} for i in range(5)
            ],
            "evolvePhase": int(detail.get("evolvePhase", 0) or 0),
            "displaySkills": skills[:4],
            "weapon": weapon,
            "bodyEquip": body_equip,
            "armEquip": arm_equip,
            "firstAccessory": first_accessory,
            "secondAccessory": second_accessory,
            "tacticalItem": tactical_item,
            "illustrationUrl": illustration_url,
            "userAvatar": binding.get("avatarUrl", "")
            if isinstance(binding, dict)
            else "",
            "userNickname": (
                binding.get("nickname") if isinstance(binding, dict) else None
            )
            or "干员",
            "userLevel": binding.get("level", 1) if isinstance(binding, dict) else 1,
            "profession": _get_val(char_data.get("profession")),
            "property": _get_val(char_data.get("property")),
            "weaponTypeName": _get_val(char_data.get("weaponType")),
            "tagsList": tags_list,
            "panelStats": panel_stats,
            "talents": talents,
            "talentNodes": talent_nodes,
            "copyright": "Endfield Plugin | AstrBot",
            "pluResPath": "file:///"
            + os.path.abspath(self.renderer.res_path).replace("\\", "/")
            + "/",
        }

    async def _prepare_gacha_icons(self, token: str, binding: dict) -> dict:
        """Fetch and aggregate icons for characters and weapons from multiple sources."""
        icon_map = {}
        # 1. From note
        try:
            note = await self.client.get_note(
                token, binding.get("role_id"), int(binding.get("server_id", 1))
            )
            if note and "chars" in note:
                for c in note["chars"]:
                    name = str(c.get("name", "")).strip()
                    url = c.get("avatarSqUrl", "") or c.get("avatar_sq_url", "")
                    if name and url:
                        icon_map[name] = url
        except:
            pass

        # 2. From pool info
        try:
            pools = await self.client.get_gacha_pool_chars()
            if pools and "pools" in pools:
                for p in pools["pools"]:
                    for lst in [
                        p.get("star6_chars", []),
                        p.get("star5_chars", []),
                        p.get("star4_chars", []),
                        p.get("star6_weapons", []),
                        p.get("star5_weapons", []),
                        p.get("star4_weapons", []),
                    ]:
                        for it in lst:
                            name = str(it.get("name", "")).strip()
                            url = it.get("cover", "") or it.get("cover_url", "")
                            if name and url:
                                icon_map[name] = url
        except:
            pass

        # 3. From Wiki (fallback)
        try:
            for stid in ["1", "2"]:
                res = await self.client.get_wiki_items(
                    {
                        "main_type_id": "1",
                        "sub_type_id": stid,
                        "page": 1,
                        "page_size": 200,
                    }
                )
                if res and "items" in res:
                    for it in res["items"]:
                        brief = it.get("brief") or it
                        name = str(brief.get("name") or "").strip()
                        url = (
                            brief.get("cover")
                            or it.get("cover")
                            or it.get("avatarSqUrl")
                            or ""
                        )
                        if name and url and name not in icon_map:
                            icon_map[name] = url
        except:
            pass
        return icon_map

    def _calculate_gacha_pity(self, records: list) -> dict:
        """Analyze records to calculate pity counts for each pool type."""
        pity = {"limited": 0, "standard": 0, "weapon": 0, "beginner": 0}
        # Records are newest first. Scan until 6-star for each pool type.
        pools_done = set()
        for r in records:
            ptype = r.get("pool_type")
            if ptype in pity and ptype not in pools_done:
                if int(r.get("rarity", 0)) >= 6:
                    pools_done.add(ptype)
                else:
                    pity[ptype] += 1
        return pity

    @filter.command("全服统计")
    async def global_gacha_stats(self, event: AstrMessageEvent):
        """全服抽卡统计"""
        # Check for optional character name argument and provider
        msg = event.message_str.strip()

        # Determine provider (skport for 国际服)
        provider = ""
        if "国际服" in msg:
            provider = "skport"
            msg = msg.replace("国际服", "").strip()

        char_name = ""
        if "全服统计" in msg:
            parts = msg.split("全服统计", 1)
            if len(parts) > 1 and parts[1].strip():
                char_name = parts[1].strip()

        # Fetch global stats
        pool_period = char_name if char_name else ""
        data = await self.client.get_gacha_global_stats(pool_period, provider=provider)

        if not data or "stats" not in data:
            yield event.plain_result(f"获取全服统计失败。")
            return

        # Check if we need to switch to a specific period
        if char_name and data.get("stats", {}).get("pool_periods"):
            periods = data["stats"]["pool_periods"]
            found = None
            for p in periods:
                names = p.get("up_char_names", [])
                pool_name = (p.get("pool_name", "")).strip()
                if (
                    any(char_name in (n or "") or (n or "") in char_name for n in names)
                    or char_name in pool_name
                    or pool_name in char_name
                ):
                    found = p
                    break
            if not found:
                yield event.plain_result(f"未找到包含 {char_name} 的期数。")
                return
            # Refetch with specific pool name
            data = await self.client.get_gacha_global_stats(
                found.get("pool_name", ""), provider=provider
            )
            if not data or "stats" not in data:
                yield event.plain_result(f"获取指定期数统计失败。")
                return

        s = data.get("stats", {})

        # Prepare render data
        total_pulls = s.get("total_pulls", 0)
        total_users = s.get("total_users", 0)
        star6 = s.get("star6_total", 0)
        star5 = s.get("star5_total", 0)
        star4 = s.get("star4_total", 0)
        avg_pity = (
            f"{s.get('avg_pity', 0):.2f}" if s.get("avg_pity") is not None else "-"
        )

        # Current UP info
        pool = s.get("current_pool", {})
        up_name = (
            pool.get("up_char_name")
            or (pool.get("up_char_names") and pool["up_char_names"][0])
            or "-"
        )
        up_char_names = (
            pool.get("up_char_names", [])
            if pool.get("up_char_names")
            else [up_name]
            if up_name != "-"
            else []
        )
        up_weapon_name = (pool.get("up_weapon_name") or "").strip()

        # Period label
        period_label = "当期 UP"
        if char_name:
            period_label = pool.get("pool_name", char_name)

        # Channel stats
        by_channel = s.get("by_channel", {})
        official_raw = by_channel.get("official")
        bilibili_raw = by_channel.get("bilibili")

        def fmt(v):
            return f"{float(v):.2f}" if v is not None else "-"

        official = None
        bilibili = None
        if official_raw:
            official = {
                "total_users": official_raw.get("total_users", 0),
                "total_pulls": official_raw.get("total_pulls", 0),
                "star6_total": official_raw.get("star6_total", 0),
                "avg_pity": fmt(official_raw.get("avg_pity")),
            }
        if bilibili_raw:
            bilibili = {
                "total_users": bilibili_raw.get("total_users", 0),
                "total_pulls": bilibili_raw.get("total_pulls", 0),
                "star6_total": bilibili_raw.get("star6_total", 0),
                "avg_pity": fmt(bilibili_raw.get("avg_pity")),
            }

        # Pool sections - need to build this first for UP rate calculation
        by_type = s.get("by_type", {})

        # UP win rate - calculated as UP char count / limited pool 6-star total
        # The UP rate shown is the percentage of UP 6-star among all 6-star in the current limited pool
        up_win_rate = "--.-"
        up_win_rate_num = 0
        up_weapon_win_rate = "--.-"
        up_weapon_win_rate_num = 0
        up_entry = None
        limited_star6 = 0

        # Get UP rate from pool_periods
        pool_periods = s.get("pool_periods", [])
        period_data = None
        if period_label == "当期 UP":
            for p in pool_periods:
                if up_name in (p.get("up_char_names", []) or []):
                    period_data = p
                    break
        else:
            for p in pool_periods:
                p_name = p.get("pool_name", "")
                if period_label == p_name or period_label in p_name:
                    period_data = p
                    break

        if period_data and period_data.get("star6_count", 0) > 0:
            up_win_rate_val = (
                period_data.get("up_count", 0) / period_data.get("star6_count")
            ) * 100
            up_win_rate = f"{up_win_rate_val:.1f}"
            up_win_rate_num = min(100, max(0, up_win_rate_val))
            logger.info(f"[全服统计] UP 计算从 pool_periods: {up_win_rate_val:.2f}%")
        else:
            # Fallback if not found in pool_periods
            up_percent = (
                pool.get("up_percent")
                or pool.get("up_rate")
                or pool.get("up_win_rate")
                or pool.get("up_percentage")
            )
            if up_percent is not None:
                up_win_rate_val = float(up_percent)
                up_win_rate = f"{up_win_rate_val:.1f}"
                up_win_rate_num = min(100, max(0, up_win_rate_val))
            else:
                limited_data = by_type.get("limited", {})
                limited_star6 = limited_data.get("star6", 0) if limited_data else 0
                ranking_limited = (
                    s.get("ranking", {}).get("limited", {}).get("six_star", [])
                )
                up_entry = None
                if up_char_names:
                    for name in up_char_names:
                        up_entry = next(
                            (
                                r
                                for r in ranking_limited
                                if (r.get("char_name") or "").strip() == name.strip()
                            ),
                            None,
                        )
                        if up_entry:
                            break

                if up_entry and up_entry.get("count") is not None and limited_star6 > 0:
                    up_win_rate_val = (up_entry.get("count", 0) / limited_star6) * 100
                    up_win_rate = f"{up_win_rate_val:.1f}"
                    up_win_rate_num = min(100, max(0, up_win_rate_val))

        # Weapon UP rate calculation
        weapon_pool_periods = s.get("weapon_pool_periods", [])
        weapon_period_data = None
        # Default active weapon pool is handled as active weapon
        for wp in weapon_pool_periods:
            if up_weapon_name in (wp.get("up_weapon_names", []) or []):
                weapon_period_data = wp
                break

        if weapon_period_data and weapon_period_data.get("star6_count", 0) > 0:
            up_weapon_win_rate_val = (
                weapon_period_data.get("up_count", 0)
                / weapon_period_data.get("star6_count")
            ) * 100
            up_weapon_win_rate = f"{up_weapon_win_rate_val:.1f}"
            up_weapon_win_rate_num = min(100, max(0, up_weapon_win_rate_val))
            logger.info(
                f"[全服统计] Weapon UP 计算从 weapon_pool_periods: {up_weapon_win_rate_val:.2f}%"
            )
        else:
            weapon_data = by_type.get("weapon", {})
        weapon_star6 = weapon_data.get("star6", 0) if weapon_data else 0

        if up_weapon_name and weapon_star6 > 0:
            ranking_weapon = s.get("ranking", {}).get("weapon", {}).get("six_star", [])
            # Try exact match first
            up_weapon_entry = next(
                (
                    r
                    for r in ranking_weapon
                    if (r.get("char_name") or "").strip() == up_weapon_name.strip()
                ),
                None,
            )
            if not up_weapon_entry:
                up_weapon_entry = next(
                    (
                        r
                        for r in ranking_weapon
                        if up_weapon_name in (r.get("char_name") or "")
                    ),
                    None,
                )
            if up_weapon_entry and up_weapon_entry.get("count") is not None:
                weapon_up_count = up_weapon_entry.get("count", 0)
                up_weapon_win_rate_val = (weapon_up_count / weapon_star6) * 100
                up_weapon_win_rate = f"{up_weapon_win_rate_val:.1f}"
                up_weapon_win_rate_num = min(100, max(0, up_weapon_win_rate_val))

        # Debug logging for troubleshooting
        logger.info(
            f"[全服统计] UP 计算调试：up_name={up_name}, up_count={up_entry.get('count') if up_entry else 'N/A'}, limited_star6={limited_star6}, up_win_rate={up_win_rate}"
        )

        # Pool sections - already defined above

        def build_distribution_list(dist_raw):
            if not dist_raw:
                return []
            max_c = max((d.get("count", 0) for d in dist_raw), default=1)
            result = []
            for d in dist_raw:
                count = d.get("count", 0)
                result.append(
                    {
                        "range": d.get("range", "-"),
                        "count": count,
                        "height": min(100, max(8, (count / max_c) * 100))
                        if max_c > 0
                        else 0,
                    }
                )
            return result

        def build_ranking_list(six_star, is_limited):
            if not six_star:
                return []
            result = []
            for r in six_star:
                char_name_r = r.get("char_name", "-")
                is_up = (
                    is_limited
                    and up_char_names
                    and any(
                        (char_name_r or "") == n or n in (char_name_r or "")
                        for n in up_char_names
                    )
                )
                result.append(
                    {
                        "char_name": char_name_r or "-",
                        "count": r.get("count", 0),
                        "percent": f"{float(r['percent']):.1f}"
                        if r.get("percent") is not None
                        else "0",
                        "isUp": is_up,
                    }
                )
            return result

        def build_pool_section(key, label, rank_top=8):
            pool_data = by_type.get(key, {})
            pool_total = pool_data.get("total", 0)
            pool_star6 = pool_data.get("star6", 0)
            p_avg_pity = (
                f"{float(pool_data['avg_pity']):.1f}"
                if pool_data.get("avg_pity") is not None
                else "-"
            )
            p_star6_rate = (
                f"{(pool_star6 / pool_total * 100):.2f}%" if pool_total > 0 else "0%"
            )

            ranking_key = (
                "weapon"
                if key == "weapon"
                else "limited"
                if key == "limited"
                else "standard"
            )
            ranking_list6 = build_ranking_list(
                s.get("ranking", {}).get(ranking_key, {}).get("six_star", []),
                key == "limited",
            )[:rank_top]
            ranking_list5 = build_ranking_list(
                s.get("ranking", {}).get(ranking_key, {}).get("five_star", []), False
            )[:rank_top]

            return {
                "label": label,
                "key": key,
                "total": pool_total,
                "star6": pool_star6,
                "star5": pool_data.get("star5", 0),
                "star4": pool_data.get("star4", 0),
                "avgPity": p_avg_pity,
                "star6Rate": p_star6_rate,
                "distributionList": build_distribution_list(
                    pool_data.get("distribution")
                ),
                "showRanking": True,
                "rankingList6": ranking_list6,
                "rankingList5": ranking_list5,
                "rankingTab6": "6 星武器" if key == "weapon" else "6 星干员",
                "rankingTab5": "5 星武器" if key == "weapon" else "5 星干员",
            }

        pool_sections = [
            build_pool_section("beginner", "新手池", 5),
            build_pool_section("standard", "常驻角色", 5),
            build_pool_section("weapon", "武器池", 5),
            build_pool_section("limited", "限定角色", 8),
        ]

        # Disable ranking for beginner pool
        pool_sections[0]["showRanking"] = False

        # Update limited pool ranking to show top 8 for 6-star and more for 5-star
        for sec in pool_sections:
            if sec["key"] == "limited":
                # Rebuild ranking lists with proper counts
                ranking_key = "limited"
                sec["rankingList6"] = build_ranking_list(
                    s.get("ranking", {}).get(ranking_key, {}).get("six_star", [])[:8],
                    True,
                )
                sec["rankingList5"] = build_ranking_list(
                    s.get("ranking", {}).get(ranking_key, {}).get("five_star", [])[:9],
                    False,
                )

        # Sync time
        sync_time = "缓存约 5 分钟" if data.get("cached") else "刚刚"
        if data.get("last_update"):
            try:
                d = datetime.datetime.fromtimestamp(int(data["last_update"]))
                sync_time = d.strftime("%Y-%m-%d %H:%M")
            except:
                sync_time = str(data["last_update"])

        render_data = {
            "title": "全服寻访统计 (国际服)"
            if provider == "skport"
            else "全服寻访统计",
            "periodLabel": period_label,
            "syncTime": sync_time,
            "totalPulls": total_pulls,
            "totalUsers": total_users,
            "star6": star6,
            "globalAvgPity": avg_pity,
            "showUpBlock": bool(
                (up_name and up_name != "-")
                or (up_weapon_name and up_weapon_name != "")
            ),
            "upName": up_name if up_name != "-" else "",
            "upWeaponName": up_weapon_name,
            "upWinRate": up_win_rate + "%",
            "upWinRateNum": up_win_rate_num,
            "upWeaponWinRate": up_weapon_win_rate + "%",
            "upWeaponWinRateNum": up_weapon_win_rate_num,
            "official": official,
            "bilibili": bilibili,
            "poolSections": pool_sections,
            "pluResPath": "file:///"
            + os.path.abspath(self.renderer.res_path).replace("\\", "/")
            + "/",
            "periodHint": "发送 :全服统计 <干员名> 可查看其他期数",
            "copyright": "Endfield Plugin | AstrBot",
        }

        try:
            url = await self.renderer.render_html(
                "gacha/global-stats.html", render_data
            )
            if url:
                yield event.image_result(url)
                return
        except Exception as e:
            logger.error(f"Global gacha stats render failed: {e}")

        # Fallback to text
        text = f"【全服抽卡统计】"
        if period_label != "当期 UP":
            text += f" · {period_label}"
        text += f"\n总抽数：{total_pulls} | 统计用户：{total_users}\n"
        text += (
            f"六星：{star6} | 五星：{star5} | 四星：{star4} | 平均出货：{avg_pity} 抽\n"
        )
        text += f"当前 UP：{up_name}\n"
        if official:
            text += f"官服：{official['total_users']} 人，{official['total_pulls']} 抽，均出 {official['avg_pity']}\n"
        if bilibili:
            text += f"B服：{bilibili['total_users']} 人，{bilibili['total_pulls']} 抽，均出 {bilibili['avg_pity']}\n"
        text += "\n发送 :全服统计 <干员名> 可查看其他期数\n"
        if data.get("cached"):
            text += "（缓存约 5 分钟）"
        yield event.plain_result(text)

    async def terminate(self):
        if self._announcement_task_handle:
            self._announcement_task_handle.cancel()
        if self._sanity_task_handle:
            self._sanity_task_handle.cancel()
        if self._ticket_task_handle:
            self._ticket_task_handle.cancel()
        if self._auto_sign_in_task_handle:
            self._auto_sign_in_task_handle.cancel()
        if self._http_client:
            await self._http_client.aclose()
        if self.client:
            await self.client.close()
        if self.renderer:
            await self.renderer.close()
        logger.info("Endfield plugin terminated.")

    async def ticket_task(self):
        """调度券满值通知推送任务"""
        logger.info("[调度券推送] 任务已启动")
        while True:
            try:
                subs = await self.ticket_mgr.get_subscriptions()
                if not subs:
                    await asyncio.sleep(600)
                    continue

                now_ts = int(time.time())
                for sub in subs:
                    user_id = sub.get("user_id")
                    msg_origin = sub.get("msg_origin", "")
                    last_notified = sub.get("last_notified", 0)

                    if not msg_origin:
                        continue
                    if now_ts - last_notified < 3600 * 4:
                        continue  # 4h cooldown

                    binding = await self.user_mgr.get_primary_binding(user_id)
                    if not binding:
                        continue

                    token = binding.get("framework_token")
                    role_id = binding.get("role_id")
                    server_id = binding.get("server_id", 1)

                    try:
                        domain_data = await self.client.get_domain(
                            token, role_id, server_id
                        )
                        if not domain_data:
                            continue

                        # Check all zones for full tickets
                        full_zones = []
                        for zone in domain_data.get("domain", []):
                            for s in zone.get("settlements", []):
                                rem = s.get("remainMoney", 0)
                                mx = s.get("moneyMax", 1)
                                try:
                                    if float(rem) >= float(mx) and float(mx) > 0:
                                        full_zones.append(s.get("name", "未知聚落"))
                                except:
                                    pass

                        if full_zones:
                            if last_notified != 0:
                                continue

                            nick = binding.get("nickname") or "干员"
                            msg = (
                                f"【调度券已满】{nick}，您的以下聚落调度券已达到上限：\n"
                                + "\n".join([f"• {z}" for z in full_zones])
                            )
                            logger.info(f"[调度券推送] 推送中: {user_id}")

                            chain = MessageChain()
                            chain.chain.append(At(qq=user_id))
                            chain.chain.append(Plain(f"\n{msg}"))

                            await self.context.send_message(msg_origin, chain)
                            await self.ticket_mgr.update_last_notified(user_id, 1)
                        else:
                            if last_notified != 0:
                                await self.ticket_mgr.update_last_notified(user_id, 0)
                    except Exception as e:
                        logger.error(f"[调度券推送] 异常 (user={user_id}): {e}")
                    await asyncio.sleep(2)

                await asyncio.sleep(1200)  # Check every 20 mins
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[调度券推送] 任务异常: {e}")
                await asyncio.sleep(60)

    @filter.command("订阅调度券")
    async def subscribe_ticket(self, event: AstrMessageEvent):
        """订阅调度券满值提醒"""
        user_id = event.get_sender_id()
        binding = await self.user_mgr.get_primary_binding(user_id)
        if not binding:
            yield event.plain_result("请先绑定森空岛账号。")
            return
        await self.ticket_mgr.add_subscription(str(user_id), event.unified_msg_origin)
        yield event.plain_result("✅ 已订阅调度券满值提醒！")

    @filter.command("取消订阅调度券")
    async def unsubscribe_ticket(self, event: AstrMessageEvent):
        """取消调度券推送"""
        user_id = event.get_sender_id()
        if await self.ticket_mgr.remove_subscription(str(user_id)):
            yield event.plain_result("已取消调度券订阅。")
        else:
            yield event.plain_result("您当前没有订阅过提醒。")
