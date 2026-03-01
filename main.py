import asyncio
import os
import time
import base64
import tempfile
from astrbot.api import logger
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register, StarTools
from astrbot.core import AstrBotConfig
from astrbot.core.message.components import Plain, Image, At

from .core.client import EndfieldClient
from .core.user import UserManager, SimulateManager, AnnouncementManager, MaaendManager, SanityManager
from .core.utils import get_message
from .core.render import Renderer

def get_cover_url(item: dict) -> str:
    if not item: return ""
    imgs = item.get("images", [])
    if not imgs: return ""
    first = imgs[0]
    if isinstance(first, str): return first
    if isinstance(first, dict):
        if first.get("url"): return first["url"]
        dis = first.get("display_infos") or first.get("displayInfos")
        if dis and isinstance(dis, list) and len(dis) > 0:
            return dis[0].get("url", "")
    return ""

def format_publish_time(ts) -> str:
    if not ts: return ""
    try:
        import datetime
        d = datetime.datetime.fromtimestamp(int(ts))
        return d.strftime("%m/%d %H:%M")
    except Exception:
        return ""

def get_content_text(data: dict) -> str:
    if not data: return ""
    texts = data.get("texts")
    if isinstance(texts, list) and texts:
        return "\n".join([str(t.get("content", "")) for t in texts if isinstance(t, dict) and t.get("content")])
    
    content = data.get("content")
    if isinstance(content, dict):
        blocks = content.get("blocks")
        if isinstance(blocks, list):
            res = []
            for b in blocks:
                if isinstance(b, dict) and b.get("kind") == "text" and "text" in b:
                    txt_prop = b["text"]
                    if isinstance(txt_prop, str): res.append(txt_prop)
                    elif isinstance(txt_prop, dict) and txt_prop.get("text"): res.append(txt_prop["text"])
            return "\n".join(res)
    return ""

def content_to_detail_html(text: str) -> str:
    if not text: return ""
    text = str(text)
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")

def build_caption_content(item: dict) -> str:
    if not item: return ""
    texts = item.get("texts")
    if not isinstance(texts, list) or not texts: return ""
    parts = []
    for text in texts:
        if isinstance(text, dict) and text.get("content"):
            parts.append(f'<div class="detail-text-block">{content_to_detail_html(text["content"])}</div>')
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
        "pageWidth": 720
    }

@register("astrbot_plugin_endfield", "bvzrays & 熵增项目组", "终末地协议终端", "v1.6.0", "https://github.com/Entropy-Increase-Team/astrbot_plugin_endfield")
class EndfieldPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig = None):
        super().__init__(context)
        self.config = config or {}
        self.api_key = config.get("api_key", "") if config else ""
        self.verify_ssl = config.get("verify_ssl", True) if config else True
        self.auto_sign_in = config.get("auto_sign_in", True) if config else True
        self.auto_sign_in_time = config.get("auto_sign_in_time", "00:05") if config else "00:05"
        self.client = EndfieldClient(self.api_key, verify_ssl=self.verify_ssl)
        
        # Use StarTools.get_data_dir() for persistence compliance
        data_dir = StarTools.get_data_dir()
        if not os.path.exists(data_dir):
            os.makedirs(data_dir, exist_ok=True)
            
        self.user_mgr = UserManager(data_dir)
        self.sim_mgr = SimulateManager(data_dir)
        self.announce_mgr = AnnouncementManager(data_dir)
        self.sanity_mgr = SanityManager(data_dir)
        self.maa_mgr = MaaendManager(data_dir)
        
        res_path = os.path.join(os.path.dirname(__file__), "resources")
        self.renderer = Renderer(res_path, self)
        self._announcement_task_handle = None
        self._sanity_task_handle = None
        self._auto_sign_in_task_handle = None
        self._http_client = None
        self.banner_cache = {}

    async def get_activity_banner(self, act: dict) -> str:
        name = act.get("name", "")
        if name in self.banner_cache:
            return self.banner_cache[name]
            
        pc_link = act.get("pc_link", "")
        import re
        match = re.search(r'gameEntryId=(\d+)', pc_link)
        if match:
            entry_id = match.group(1)
            try:
                res = await self.client._get(f"/api/wiki/items/{entry_id}")
                if res and isinstance(res, dict) and "content" in res and "document_map" in res["content"]:
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
        import mimetypes, base64, httpx, hashlib, asyncio
        if not rp:
            return ""
            
        if rp.startswith("//"):
            rp = "https:" + rp
            
        cache_dir = os.path.join(self.renderer.res_path, "cache")
        if not os.path.exists(cache_dir):
            os.makedirs(cache_dir, exist_ok=True)
            
        if rp.startswith("http://") or rp.startswith("https://"):
            url_hash = hashlib.md5(rp.encode()).hexdigest()
            # Try to guess extension from URL or use .png as default
            ext = ".png"
            if "." in rp.split("/")[-1]:
                ext = "." + rp.split("/")[-1].split(".")[-1].split("?")[0]
                if len(ext) > 5: ext = ".png" # Fix for weird query params
                
            # Strict SSRF prevention using actual IP resolution
            from urllib.parse import urlparse
            import socket, ipaddress
            
            try:
                parsed_url = urlparse(rp)
                hostname = parsed_url.hostname
                if not hostname:
                    return ""
                    
                addr_info = socket.getaddrinfo(hostname, None)
                for addr in addr_info:
                    ip = addr[4][0]
                    ip_obj = ipaddress.ip_address(ip)
                    if ip_obj.is_private or ip_obj.is_loopback or ip_obj.is_unspecified or ip_obj.is_link_local or ip_obj.is_multicast:
                        logger.warning(f"Blocked potential SSRF access to {rp} (Resolved IP: {ip})")
                        return ""
            except Exception as e:
                logger.warning(f"Blocked potential SSRF access to {rp} due to resolving error: {e}")
                return ""
                
            cache_file = os.path.join(cache_dir, f"{url_hash}{ext}")
            
            if os.path.exists(cache_file):
                # Return file:/// URI for Playwright to load directly from disk (much faster)
                return "file:///" + os.path.abspath(cache_file).replace("\\", "/")
                    
            try:
                # Use persistent client session if possible
                if self._http_client is None or self._http_client.is_closed:
                    self._http_client = httpx.AsyncClient(verify=self.verify_ssl)
                
                client = self._http_client
                for attempt in range(3):
                    try:
                        resp = await client.get(rp, timeout=10)
                        if resp.status_code == 200:
                            with open(cache_file, "wb") as f:
                                f.write(resp.content)
                            return "file:///" + os.path.abspath(cache_file).replace("\\", "/")
                        break
                    except Exception:
                        if attempt == 2: raise
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
        if not urls: return []
        sem = asyncio.Semaphore(10)
        async def _download(u):
            async with sem:
                return await self.get_b64(u)
        tasks = [_download(url) for url in urls]
        return await asyncio.gather(*tasks)

    async def initialize(self):
        # Announcement Task
        self._announcement_task_handle = asyncio.create_task(self.announcement_task())
        self._sanity_task_handle = asyncio.create_task(self.sanity_task())
        # Auto Sign-in Task
        if self.auto_sign_in:
            self._auto_sign_in_task_handle = asyncio.create_task(self.auto_sign_in_task())

    @filter.command("zmd")
    async def zmd_help(self, event: AstrMessageEvent):
        '''显示终末地插件帮助菜单'''
        render_data = {
            "helpCfg": {
                "title": "终末地协议终端",
                "subTitle": "Endfield Protocol Terminal"
            },
            "helpGroup": [
                {
                    "type": "tips",
                    "items": [
                        {"title": "提示", "text": "指令触发符：/ (默认) 或 :"}
                    ]
                },
                {
                    "group": "账号绑定",
                    "list": [
                        {"title": ":授权登陆", "desc": "网页授权登录", "icon": True},
                        {"title": ":扫码绑定", "desc": "扫码快捷登录", "icon": True},
                        {"title": ":手机绑定 [手机号]", "desc": "验证码登录", "icon": True},
                        {"title": ":绑定列表", "desc": "查看已绑定账号", "icon": True},
                        {"title": ":切换绑定 [序号]", "desc": "切换当前账号", "icon": True},
                        {"title": ":删除绑定 [序号]", "desc": "删除绑定账号", "icon": True}
                    ]
                },
                {
                    "group": "信息查询",
                    "list": [
                        {"title": ":便签", "desc": "查询理智与日常活跃", "icon": True},
                        {"title": ":理智", "desc": "快捷查询理智状态", "icon": True},
                        {"title": ":干员列表", "desc": "查询干员列表", "icon": True},
                        {"title": ":<干员名>面板", "desc": "查询干员详细面板", "icon": True}
                    ]
                },
                {
                    "group": "其他功能",
                    "list": [
                        {"title": ":公告", "desc": "查看最新官方公告", "icon": True},
                        {"title": ":wiki 干员 [名称]", "desc": "查询干员百科", "icon": True},
                        {"title": ":订阅理智", "desc": "满理智提醒", "icon": True},
                        {"title": ":取消订阅理智", "desc": "取消满理智提醒", "icon": True},
                        {"title": ":日历", "desc": "查看最新活动日历图", "icon": True}
                    ]
                }
            ],
            "contentWidth": 800,
            "colCount": 2,
            "colWidth": 330,
            "widthGap": 20,
            "copyright": "Endfield Protocol Terminal | v1.4.0",
            "pluResPath": "file:///" + os.path.abspath(self.renderer.res_path).replace("\\", "/") + "/"
        }
        
        try:
            img_url = await self.renderer.render_html("help/help.html", render_data)
            if img_url:
                yield event.image_result(img_url)
                return
        except Exception as e:
            logger.warning(f"渲染菜单失败: {e}")
            
        # Fallback to plain text if rendering fails
        help_text = "【终末地协议终端 v1.4.0】\n"
        for group in render_data["helpGroup"]:
            if group.get("group"):
                help_text += f"\n{group['group']}\n"
            if group.get("list"):
                for item in group["list"]:
                    help_text += f" {item['title']} - {item['desc']}\n"
        yield event.plain_result(help_text)

    @filter.command("绑定列表")
    async def bind_list(self, event: AstrMessageEvent):
        '''查看已绑定账号'''
        user_id = event.get_sender_id()
        bindings = await self.user_mgr.get_user_bindings(user_id)
        if not bindings:
            yield event.plain_result("暂无绑定账号。")
            return
            
        # Try to render image
        items = []
        for i, b in enumerate(bindings):
            items.append({
                "index": i + 1,
                "nickname": b.get("nickname", "未知"),
                "role_id": b.get("role_id", "未知"),
                "server_label": "官服" if b.get("server_id") == 1 else "B服",
                "type_label": b.get("login_type", "未知"),
                "isPrimary": b.get("is_primary", False),
                "created_at": time.strftime("%Y-%m-%d %H:%M", time.localtime(b.get("bind_time", 0)/1000)) if b.get("bind_time") else "未知"
            })
            
        render_data = {
            "title": "终末地绑定列表",
            "subtitle": f"共 {len(bindings)} 个绑定",
            "bindings": items,
            "copyright": "Endfield Plugin | AstrBot"
        }
        
        try:
            img_url = await self.renderer.render_html("enduid/bind-list.html", render_data)
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
        '''切换当前使用的账号'''
        user_id = event.get_sender_id()
        bindings = await self.user_mgr.get_user_bindings(user_id)
        if not (1 <= index <= len(bindings)):
            yield event.plain_result(f"序号错误，请选择 1 到 {len(bindings)} 之间的数字。")
            return
            
        for i, b in enumerate(bindings):
            b["is_primary"] = (i + 1 == index)
            
        await self.user_mgr.save_user_bindings(user_id, bindings)
        yield event.plain_result(f"已切换至账号：{bindings[index-1]['nickname']}")
        async for res in self.bind_list(event):
            yield res

    @filter.command("删除绑定")
    async def delete_bind(self, event: AstrMessageEvent, index: int):
        '''删除指定绑定账号'''
        user_id = event.get_sender_id()
        bindings = await self.user_mgr.get_user_bindings(user_id)
        if not (1 <= index <= len(bindings)):
            yield event.plain_result(f"序号错误，请选择 1 到 {len(bindings)} 之间的数字。")
            return
            
        target = bindings[index-1]
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

    @filter.command("授权登陆")
    async def auth_login(self, event: AstrMessageEvent):
        '''网页授权登录'''
        if not self.config.get("api_key"):
            yield event.plain_result(get_message("common.need_api_key"))
            return

        user_id = event.get_sender_id()
        client_name = self.config.get("auth_client_name", "终末地机器人")
        
        auth_req = await self.client.create_authorization_request(
            client_id=user_id,
            client_name=client_name
        )

        if not auth_req or "request_id" not in auth_req or "auth_url" not in auth_req:
            yield event.plain_result("创建授权请求失败，请检查网络或 API Key。")
            return

        request_id = auth_req["request_id"]
        auth_url = auth_req["auth_url"]
        
        # Format response message
        msg = f"{get_message('enduid.auth_link_intro')}\n{auth_url}\n\n{get_message('enduid.auth_link_wait')}"
        yield event.plain_result(msg)

        # Polling for status
        max_attempts = 60
        auth_data = None
        for _ in range(max_attempts):
            await asyncio.sleep(3)
            status = await self.client.get_authorization_request_status(request_id)
            if not status:
                continue
            
            state = status.get("status")
            if state in ["used", "approved"]:
                if status.get("framework_token"):
                    auth_data = status
                    break
            elif state == "rejected":
                yield event.plain_result(get_message("enduid.auth_rejected"))
                return
            elif state == "expired":
                yield event.plain_result(get_message("enduid.auth_expired"))
                return

        if not auth_data or not auth_data.get("framework_token"):
            yield event.plain_result(get_message("enduid.auth_timeout"))
            return

        # Create binding in unified backend
        token = auth_data["framework_token"]
        binding_res = await self.client.create_binding(token, user_id)
        
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
            "is_active": True,
            "is_primary": True,
            "login_type": "auth",
            "bind_time": int(time.time() * 1000), # Simple TS
            "last_sync": int(time.time() * 1000)
        }
        
        existing = await self.user_mgr.get_user_bindings(user_id)
        existing.append(new_account)
        await self.user_mgr.save_user_bindings(user_id, existing)
        
        yield event.plain_result(get_message("enduid.login_ok", {
            "nickname": new_account["nickname"],
            "role_id": new_account["role_id"],
            "server_id": "官服" if new_account["server_id"] == 1 else "B服",
            "count": len(await self.user_mgr.get_user_bindings(user_id))
        }))

    @filter.command("扫码绑定")
    async def qr_login(self, event: AstrMessageEvent):
        '''扫码快捷登录'''
        user_id = event.get_sender_id()
        qr_data = await self.client.get_qr()
        if not qr_data or "qrcode" not in qr_data or "framework_token" not in qr_data:
            yield event.plain_result("获取二维码失败。")
            return
            
        token = qr_data["framework_token"]
        qr_b64 = qr_data["qrcode"] # data:image/png;base64,...
        
        # In AstrBot, we can send a base64 image or a temp file.
        # We'll use a temp file for compatibility.
        
        img_data = base64.b64decode(qr_b64.split(",")[-1])
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            tmp.write(img_data)
            tmp_path = tmp.name
            
        yield event.chain_result([
            Image.fromFileSystem(tmp_path),
            Plain("请使用森空岛 APP 扫描二维码进行登录。\n二维码有效时间约 3 分钟。")
        ])
        
        # Polling
        max_attempts = 90
        login_data = None
        
        try:
            for _ in range(max_attempts):
                await asyncio.sleep(2)
                status = await self.client.get_qr_status(token)
                if not status: continue
                
                if status.get("status") == "done":
                    login_data = await self.client.confirm_qr_login(token, user_id)
                    if login_data and login_data.get("framework_token"):
                        break
                elif status.get("status") in ["expired", "failed"]:
                    yield event.plain_result("二维码已过期或登录失败。")
                    return
            
            if not login_data:
                yield event.plain_result("登录超时。")
                return
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            
        # Create binding
        auth_token = login_data["framework_token"]
        binding_res = await self.client.create_binding(auth_token, user_id)
        if not binding_res:
            yield event.plain_result("创建绑定失败。")
            return
            
        # Save (simplified logic, same as auth_login)
        acc = {
            "framework_token": auth_token,
            "binding_id": binding_res.get("id"),
            "role_id": str(binding_res.get("role_id", "")),
            "nickname": binding_res.get("nickname", "未知"),
            "server_id": binding_res.get("server_id", 1),
            "login_type": "qr",
            "is_active": True,
            "is_primary": True,
            "bind_time": int(time.time() * 1000),
            "last_sync": int(time.time() * 1000)
        }
        existing = await self.user_mgr.get_user_bindings(user_id)
        existing.append(acc)
        await self.user_mgr.save_user_bindings(user_id, existing)
        
        yield event.plain_result(get_message("enduid.login_ok", {
            "nickname": acc["nickname"],
            "role_id": acc["role_id"],
            "server_id": "官服" if acc["server_id"] == 1 else "B服",
            "count": len(await self.user_mgr.get_user_bindings(user_id))
        }))

    @filter.command("手机绑定")
    async def phone_login(self, event: AstrMessageEvent, phone: str):
        '''手机号验证码登录'''
        if event.get_group_id():
            yield event.plain_result("手机号登录请在私聊中进行。")
            return
            
        success = await self.client.phone_send_code(phone)
        if not success:
            yield event.plain_result("发送验证码失败。")
            return
            
        yield event.plain_result(f"验证码已发送至 {phone[:3]}****{phone[7:]}，请输入 6 位验证码。")
        
        # AstrBot Session Waiter
        from astrbot.core.utils.session_waiter import session_waiter, SessionController
        
        @session_waiter(timeout=300)
        async def waiter(controller: SessionController, waiter_event: AstrMessageEvent):
            code = waiter_event.message_str.strip()
            if not code.isdigit() or len(code) != 6:
                return # Keep waiting
                
            login_data = await self.client.phone_verify_code(phone, code)
            if not login_data or not login_data.get("framework_token"):
                await waiter_event.send("验证码错误或登录失败。")
                controller.stop()
                return
                
            token = login_data["framework_token"]
            binding_res = await self.client.create_binding(token, event.get_sender_id())
            if not binding_res:
                await waiter_event.send("创建绑定失败。")
                controller.stop()
                return
                
            # Save
            acc = {
                "framework_token": token,
                "binding_id": binding_res.get("id"),
                "role_id": str(binding_res.get("role_id", "")),
                "nickname": binding_res.get("nickname", "未知"),
                "server_id": binding_res.get("server_id", 1),
                "login_type": "phone",
                "is_active": True,
                "is_primary": True,
                "bind_time": int(time.time() * 1000),
                "last_sync": 0,
            }
            existing = await self.user_mgr.get_user_bindings(event.get_sender_id())
            existing.append(acc)
            await self.user_mgr.save_user_bindings(event.get_sender_id(), existing)
            
            await waiter_event.send(get_message("enduid.login_ok", {
                "nickname": acc["nickname"],
                "role_id": acc["role_id"],
                "server_id": "官服" if acc["server_id"] == 1 else "B服",
                "count": len(await self.user_mgr.get_user_bindings(event.get_sender_id()))
            }))
            controller.stop()
            
        try:
            await waiter(event)
        except TimeoutError:
            yield event.plain_result("验证超时。")
    @filter.command("理智")
    async def stamina(self, event: AstrMessageEvent):
        '''查询理智状态'''
        user_id = event.get_sender_id()
        binding = await self.user_mgr.get_primary_binding(user_id)
        if not binding:
            yield event.plain_result("未绑定账号，请输入 :帮助 查看绑定方式。")
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
            full_time = time.strftime("%H:%M", time.localtime(time.time() + recover_seconds))
        else:
            full_time = "未知"
        
        user_name = binding.get('nickname')
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
            "staminaPercent": (s_current / max(s_max, 1)) * 100,
            "fullTime": full_time,
            "activation": a_current,
            "maxActivation": a_max,
            "activationPercent": (a_current / max(a_max, 1)) * 100,
        }
        
        op_dir = os.path.join(self.renderer.res_path, "img", "operator")
        if os.path.exists(op_dir):
            ops = [f for f in os.listdir(op_dir) if f.endswith(('.png', '.jpg', '.webp'))]
            if ops:
                import random
                op_file = random.choice(ops)
                acc_data["operatorImg"] = await self.get_b64(os.path.join("img", "operator", op_file))
        
        acc_data["staminaBgImg"] = await self.get_b64("img/stbg.png")

        render_data = {
            "accounts": [acc_data],
            "pluResPath": "file:///" + os.path.abspath(self.renderer.res_path).replace("\\", "/") + "/",
            "copyright": "Endfield Plugin | AstrBot"
        }
        
        try:
            img_url = await self.renderer.render_html("stamina/stamina.html", render_data)
            if img_url:
                yield event.image_result(img_url)
                return
        except Exception as e:
            logger.warning(f"渲染理智失败，使用文本回退: {e}")
        yield event.plain_result(f"【{acc_data['userName']}】\n理智：{acc_data['current']}/{acc_data['max']}\n活跃度：{acc_data['activation']}/{acc_data['maxActivation']}\n回满时间：{acc_data['fullTime']}")

    @filter.command("便签")
    async def note(self, event: AstrMessageEvent):
        '''查询角色便签'''
        user_id = event.get_sender_id()
        binding = await self.user_mgr.get_primary_binding(user_id)
        if not binding:
            yield event.plain_result("未绑定账号，请输入 :帮助 查看绑定方式。")
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
        server_name = base.get("serverName", "未知")
        
        stamina_obj = stamina_data.get("stamina", {}) if stamina_data else {}
        daily_obj = stamina_data.get("dailyMission", {}) if stamina_data else {}
        
        s_current = stamina_obj.get("current", "—")
        s_max = stamina_obj.get("max", "—")
        a_current = daily_obj.get("activation", "—")
        a_max = daily_obj.get("maxActivation", 100)
        
        create_time = base.get("createTime")
        if create_time:
            create_time_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(int(create_time)))
            awakening_date_str = time.strftime("%Y-%m-%d", time.localtime(int(create_time)))
        else:
            create_time_str = "未知"
            awakening_date_str = ""
            
        last_login = base.get("lastLoginTime")
        last_login_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(int(last_login))) if last_login else "未知"
        
        # Pre-download all character icons in parallel
        icon_urls = []
        for c in chars:
            icon_urls.append(c.get("avatarSqUrl") or c.get("avatar_sq_url") or "")
        icon_local_paths = await self.parallel_download_b64(icon_urls)
            
        chars_list = []
        for i, c in enumerate(chars):
            chars_list.append({
                "name": c.get("name", "未知"),
                "sqUrl": icon_local_paths[i]
            })
            
        render_data = {
            "title": "终末地便签",
            "subtitle": f"{base.get('name', '未知')} · {server_name}",
            "base": {
                "name": base.get("name", "未知"),
                "roleId": base.get("roleId", "未知"),
                "level": base.get("level", 0),
                "exp": base.get("exp", 0),
                "worldLevel": base.get("worldLevel", 0),
                "serverName": server_name,
                "createTimeStr": create_time_str,
                "lastLoginTimeStr": last_login_str,
                "mainMissionDesc": base.get("mainMission", {}).get("description", "未知"),
                "avatarUrl": await self.get_b64(base.get("avatarUrl", "")),
                "awakeningDateStr": awakening_date_str
            },
            "stats": {
                "charNum": base.get("charNum", 0),
                "weaponNum": base.get("weaponNum", 0),
                "docNum": base.get("docNum", 0),
                "staminaCurrent": s_current,
                "staminaMax": s_max,
                "activation": a_current,
                "maxActivation": a_max
            },
            "chars": chars_list,
            "pluResPath": "file:///" + os.path.abspath(self.renderer.res_path).replace("\\", "/") + "/"
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
        '''每日签到'''
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

    @filter.command("干员列表")
    async def operator_list(self, event: AstrMessageEvent):
        '''查询干员列表'''
        user_id = event.get_sender_id()
        binding = await self.user_mgr.get_primary_binding(user_id)
        if not binding:
            yield event.plain_result("未绑定账号。")
            return
            
        yield event.plain_result("正在查询干员列表...")
        
        token = binding.get("framework_token")
        res = await self.client.get_card_detail(token, binding.get("role_id"), binding.get("server_id", 1))
        
        if not res or "detail" not in res:
            yield event.plain_result("查询失败。")
            return
            
        detail = res["detail"]
        chars = detail.get("chars", [])
        if not chars:
            yield event.plain_result("未查询到干员数据。")
            return

        color_codes = {
            "PHYSICAL": "PHY", "ARTS": "ART", "TRUE": "TRU",
            "STRIKER": "STR", "CASTER": "CAS", "SUPPORT": "SUP",
            "DEFENDER": "DEF", "SNIPER": "SNI", "MEDIC": "MED",
            "VANGUARD": "VAN", "SPECIALIST": "SPE"
        }

        # Pre-download all operator illustrations in parallel
        illustration_urls = [char_data.get("avatarRtUrl", "") for c in chars for char_data in [c.get("charData", c)]]
        local_illustrations = await self.parallel_download_b64(illustration_urls)

        operators = []
        for i, c in enumerate(chars):
            char_data = c.get("charData", c)
            prof = char_data.get("profession", {}).get("value", "")
            prop = char_data.get("property", {}).get("value", "")
            operators.append({
                "name": char_data.get("name", "未知"),
                "nameChars": list(char_data.get("name", "未知")),
                "rarity": int(char_data.get("rarity", {}).get("value", 1)) if isinstance(char_data.get("rarity"), dict) else 1,
                "level": c.get("level", 0),
                "imageUrl": local_illustrations[i],
                "profession": prof,
                "property": prop,
                "professionIcon": await self.get_b64(f"meta/class/{prof}.jpg") if prof else "",
                "propertyIcon": await self.get_b64(f"meta/attrpanle/{prop}.jpg") if prop else "",
                "phaseIcon": await self.get_b64(f"meta/phases/phase-{c.get('evolvePhase', 0)}.png"),
                "potentialLevel": c.get('potentialLevel', 0),
                "colorCode": color_codes.get(prop, "PHY")
            })
            
        operators.sort(key=lambda x: (x["rarity"], x["level"]), reverse=True)
        
        # Calculate layout constraints
        list_card_width = 300
        list_column_count = 6
        list_gap_px = 12
        list_content_width = (list_card_width * list_column_count) + (list_gap_px * (list_column_count - 1))
        list_page_width = list_content_width + 56 # 28px padding on each side (matches top-bar)
        
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
            "userAvatar": await self.get_b64(detail.get("base", {}).get("avatarUrl", "")),
            "listBgFile": list_bg_file,
            "pluResPath": "file:///" + os.path.abspath(self.renderer.res_path).replace("\\", "/") + "/",
            "copyright": "Endfield Plugin | AstrBot",
            "listPageWidth": list_page_width,
            "listContentWidth": list_content_width,
            "listColumnCount": list_column_count,
            "listCardWidthPx": list_card_width,
            "listGapPx": list_gap_px,
            "listCardScale": list_card_width / 800.0
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
        if len(operators) > 20: msg += "..."
        yield event.plain_result(msg)

    @filter.regex(r"^\*?(?:终末地)?\s*(.+?)\s*(?:终末地)?面板$")
    async def operator_panel(self, event: AstrMessageEvent, char_name: str):
        '''查询干员详细面板'''
        user_id = event.get_sender_id()
        binding = await self.user_mgr.get_primary_binding(user_id)
        if not binding:
            yield event.plain_result("未绑定账号。")
            return
            
        yield event.plain_result(f"正在查询 {char_name} 的面板...")
        
        token = binding.get("framework_token")
        # 1. Fetch note to find the character matched by name
        note = await self.client.get_note(token, binding.get("role_id"), binding.get("server_id", 1))
        if not note or "chars" not in note:
            yield event.plain_result("获取干员列表失败。")
            return
            
        chars = note["chars"]
        matched = next((c for c in chars if char_name in c.get("name", "")), None)
        if not matched:
            yield event.plain_result(f"未在当前账号找到干员 {char_name}。")
            return
            
        inst_id = matched.get("id")
        # 2. Fetch full detail
        full_res = await self.client.get_card_char(token, inst_id)
        if not full_res or "detail" not in full_res:
             yield event.plain_result("获取面板详情失败。")
             return
             
        detail = full_res["detail"]
        operator = detail.get("char", {})
        char_data = operator.get("charData", {})
        
        # 3. Prepare render data (simplified extraction)
        rarity = int(char_data.get("rarity", {}).get("value", 1))
        render_data = {
            "name": char_data.get("name"),
            "level": operator.get("level", 0),
            "stars": list(range(1, rarity + 1)),
            "profession": char_data.get("profession", {}).get("value"),
            "property": char_data.get("property", {}).get("value"),
            "illustrationUrl": char_data.get("illustrationUrl") or char_data.get("avatarRtUrl"),
            "pluResPath": "file:///" + os.path.abspath(self.renderer.res_path).replace("\\", "/") + "/",
            "copyright": "Endfield Plugin | AstrBot"
        }
        
        try:
            img_url = await self.renderer.render_html("operator/operator.html", render_data)
            if img_url:
                yield event.image_result(img_url)
                return
        except Exception as e:
            logger.warning(f"渲染干员面板失败，使用文本回退: {e}")
        yield event.plain_result(f"【{char_name}】Lv.{render_data['level']} ({rarity}星)")

    @filter.command("抽卡记录")
    async def gacha_records(self, event: AstrMessageEvent, page: int = 1):
        '''查询抽卡记录'''
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
            "userNickname": binding.get('nickname') or "未知",
            "userUid": binding.get("role_id", ""),
            "userAvatar": await self.get_b64(binding.get("avatarUrl", "")),
            "page": page,
            "pageSize": 10,
            "poolSections": [],
            "copyright": "Endfield Plugin | AstrBot"
        }
        
        if records_res and "records" in records_res:
            render_data["poolSections"].append({
                "label": "最近记录",
                "total": records_res.get("total", len(records_res["records"])),
                "page": page,
                "pages": records_res.get("pages", 1),
                "hasRecords": len(records_res["records"]) > 0,
                "records": [{
                    "index": i + 1 + (page - 1) * 10,
                    "rarity": r.get('rarity'),
                    "starClass": f"star{r.get('rarity')}",
                    "name": r.get('char_name') or r.get('item_name'),
                    "isUp": False
                } for i, r in enumerate(records_res["records"])]
            })
            
            try:
                img_url = await self.renderer.render_html("gacha/gacha-record.html", render_data)
                if img_url:
                    yield event.image_result(img_url)
                    return
            except Exception as e:
                logger.warning(f"渲染抽卡记录失败，使用文本回退: {e}")
                
            msg += f"\n最近记录 (第 {page} 页):\n"
            for r in records_res["records"]:
                msg += f"- ★{r.get('rarity')} {r.get('char_name') or r.get('item_name')}\n"
        
        yield event.plain_result(msg)

    @filter.command("抽卡分析同步")
    async def gacha_sync(self, event: AstrMessageEvent):
        '''同步抽卡记录并分析'''
        user_id = event.get_sender_id()
        binding = await self.user_mgr.get_primary_binding(user_id)
        if not binding:
            yield event.plain_result("未绑定账号。请先使用 /终末地扫码登录。")
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
        for _ in range(20):
            await asyncio.sleep(2)
            status_res = await self.client.get_gacha_sync_status(token)
            if not status_res: continue
            
            status = status_res.get("status")
            if status == "completed":
                new_records = status_res.get("new_records", 0)
                yield event.plain_result(f"同步完成！拉取到 {new_records} 条新记录。正在生成分析图...")
                # Call analysis
                async for res in self.gacha_analysis(event):
                    yield res
                return
            elif status == "failed":
                yield event.plain_result(f"同步失败: {status_res.get('error', '未知错误')}")
                return
                
        yield event.plain_result("同步耗时过长，已在后台继续进行，请稍后使用【/抽卡分析】查看。")

    @filter.command("抽卡分析")
    async def gacha_analysis(self, event: AstrMessageEvent):
        '''生成抽卡分析图'''
        user_id = event.get_sender_id()
        binding = await self.user_mgr.get_primary_binding(user_id)
        if not binding:
            yield event.plain_result("未绑定账号。")
            return
            
        token = binding.get("framework_token")
        statsData = await self.client.get_gacha_stats(token)
        
        if not statsData or not statsData.get("stats", {}).get("total_count", 0):
            yield event.plain_result("暂无抽卡数据，请先发送【/抽卡分析同步】获取数据。")
            return
            
        stats = statsData.get("stats", {})
        
        # We need to fetch all records to calculate pity for each pool type
        import datetime
        now = datetime.datetime.now()
        analysisTime = now.strftime("%Y-%m-%d %H:%M")
        
        charAvatarMap = {}
        target_avatar = binding.get("avatarUrl", "")
        try:
            note_res = await self.client.get_note(token, binding.get("role_id"), binding.get("server_id", 1))
            if note_res and "base" in note_res and note_res["base"].get("avatarUrl"):
                target_avatar = note_res["base"]["avatarUrl"]
            if note_res and "chars" in note_res:
                for c in note_res.get("chars", []):
                    name = str(c.get("name", "")).strip()
                    url = c.get("avatarSqUrl", "") or c.get("avatar_sq_url", "")
                    if name and url: charAvatarMap[name] = url
        except Exception:
            pass
            
        pool_chars_data = None
        try:
            pool_chars_data = await self.client.get_gacha_pool_chars()
            if pool_chars_data and "pools" in pool_chars_data:
                for p in pool_chars_data.get("pools", []):
                    # Combine character and weapon lists to ensure all icons are captured
                    item_lists = [
                        p.get("star6_chars", []), p.get("star5_chars", []), p.get("star4_chars", []),
                        p.get("star6_weapons", []), p.get("star5_weapons", []), p.get("star4_weapons", [])
                    ]
                    for lst in item_lists:
                        for c in lst:
                            name = str(c.get("name", "")).strip()
                            cover = c.get("cover", "") or c.get("cover_url", "")
                            if name and cover and name not in charAvatarMap:
                                charAvatarMap[name] = cover
        except Exception:
            pass
            
        try:
            # sub_type_id 1 is characters, 2 is weapons
            tasks = [
                self.client.get_wiki_items({"main_type_id": "1", "sub_type_id": "1", "page": 1, "page_size": 200}),
                self.client.get_wiki_items({"main_type_id": "1", "sub_type_id": "2", "page": 1, "page_size": 200})
            ]
            wiki_responses = await asyncio.gather(*tasks)
            for wiki_res in wiki_responses:
                if wiki_res and "items" in wiki_res:
                    for it in wiki_res.get("items", []):
                        brief = it.get("brief") or it
                        name = str(brief.get("name") or it.get("name") or "").strip()
                        cover = brief.get("cover") or it.get("cover") or it.get("avatarSqUrl") or ""
                        if name and cover and name not in charAvatarMap:
                            charAvatarMap[name] = cover
        except Exception:
            pass

        render_data = {
            "title": "抽卡分析",
            "subtitle": "个人数据",
            "totalCount": stats.get("total_count", 0),
            "star6": stats.get("star6_count", 0),
            "star5": stats.get("star5_count", 0),
            "star4": stats.get("star4_count", 0),
            "userNickname": binding.get('nickname') or "未知",
            "userUid": binding.get("role_id", ""),
            "userAvatar": await self.get_b64(target_avatar) if target_avatar else "",
            "analysisTime": analysisTime,
            "syncHint": "若需刷新，发送 :抽卡分析同步",
            "pluResPath": "file:///" + os.path.abspath(self.renderer.res_path).replace("\\", "/") + "/",
            "poolGroups": [],
            "copyright": "Endfield Plugin | AstrBot"
        }
        
        # Build UP characters map from pool info
        pool_up_map = {}
        try:
            if pool_chars_data and "pools" in pool_chars_data:
                for p in pool_chars_data.get("pools", []):
                    pname = str(p.get("pool_name", "")).strip()
                    if pname:
                        ups = [str(c.get("name", "")).strip() for c in p.get("star6_chars", []) if c.get("is_up")]
                        pool_up_map[pname] = ups
        except Exception:
            pass

        pool_types = [
            {"key": "limited", "label": "限定角色"},
            {"key": "standard", "label": "常驻角色"}, 
            {"key": "weapon", "label": "武器池"},
            {"key": "beginner", "label": "新手池"}
        ]
            
        char_pools = []
        weapon_pools = []
        
        for ptype in pool_types:
            key = ptype["key"]
            records_res = await self.client.get_gacha_records(token, pools=key, limit=500)
            records = records_res.get("records", []) if records_res else []
            
            if not records: continue
            
            # Group by pool_name
            pools_dict = {}
            free_pools_dict = {}
            for r in records:
                pool_name = str(r.get("pool_name", "")).strip() or "未知"
                if r.get("is_free"):
                    if pool_name not in free_pools_dict:
                        free_pools_dict[pool_name] = []
                    free_pools_dict[pool_name].append(r)
                else:
                    if pool_name not in pools_dict:
                        pools_dict[pool_name] = []
                    pools_dict[pool_name].append(r)
                
            for pool_name, pool_records in pools_dict.items():
                # Sort asc to calculate pity
                pool_records.sort(key=lambda x: str(x.get("seq_id", "")), reverse=False)
                
                free_records = free_pools_dict.get(pool_name, [])
                free_total = len(free_records)
                
                total = len(pool_records)
                star6_count = 0
                images = []
                # Pre-collect all 5/6-star icon URLs for this pool
                all_needed_names = []
                for r in pool_records:
                    if r.get("rarity", 0) >= 5:
                        all_needed_names.append(str(r.get("char_name") or r.get("item_name", "")).strip())
                for r in free_records:
                    if r.get("rarity", 0) >= 5:
                        all_needed_names.append(str(r.get("char_name") or r.get("item_name", "")).strip())
                
                # Download icons in parallel
                urls_to_download = [charAvatarMap.get(name, "") for name in all_needed_names]
                local_icons = await self.parallel_download_b64(urls_to_download)
                icon_map = dict(zip(all_needed_names, local_icons))

                images = []
                pity_since_last = 0
                max_pity = 40 if key == "weapon" else 80
                
                for r in pool_records:
                    pity_since_last += 1
                    rarity = r.get("rarity")
                    if rarity == 6:
                        star6_count += 1
                        item_name = str(r.get("char_name") or r.get("item_name", "")).strip()
                        up_list = pool_up_map.get(pool_name, [])
                        is_up = item_name in up_list or "UP" in str(r.get("item_name", ""))
                        
                        bar_percent = min(100, int((pity_since_last / max_pity) * 100))
                        
                        images.append({
                            "name": item_name,
                            "pullCount": pity_since_last,
                            "tag": "UP" if is_up else "歪",
                            "badgeColor": "up" if is_up else "normal",
                            "barPercent": bar_percent,
                            "barColorLevel": "green" if pity_since_last < (max_pity*0.6) else "yellow" if pity_since_last < (max_pity*0.9) else "red",
                            "url": icon_map.get(item_name, ""),
                            "fiveStars": [], # Empty list after removing markers
                            "refLinePercent": None
                        })
                        pity_since_last = 0
                        
                # Reverse images for display (newest first)
                images.reverse()
                
                # Insert free pulls into images if they hit 6 star
                for r in free_records:
                    if r.get("rarity") == 6:
                        pass_name = str(r.get("char_name") or r.get("item_name", "")).strip()
                        images.append({
                            "name": pass_name,
                            "pullCount": "免费",
                            "tag": "免费",
                            "badgeColor": "free",
                            "barPercent": 100,
                            "barColorLevel": "green",
                            "url": icon_map.get(pass_name, ""),
                            "fiveStars": [],
                            "refLinePercent": None
                        })
                
                entry = {
                    "poolName": pool_name,
                    "total": total + free_total,
                    "star6": star6_count,
                    "metric1Label": "平均花费",
                    "metric1": f"{total // star6_count}抽" if star6_count > 0 else "-",
                    "metric2Label": "未出红",
                    "metric2": f"{pity_since_last}抽",
                    "images": images,
                    "pitySinceLast6": pity_since_last,
                    "pityBarPercent": min(100, int((pity_since_last / max_pity) * 100)),
                    "pityBarColorLevel": "green" if pity_since_last < (max_pity*0.6) else "yellow" if pity_since_last < (max_pity*0.9) else "red",
                    "pityFiveStars": [],
                    "freeTotal": free_total,
                    "inheritedPity": 0,
                    "inheritedPityPercent": 0,
                    "freeBarPercent": min(100, int((free_total / 10) * 100)) if free_total > 0 else 0
                }
                
                if key == "weapon":
                    weapon_pools.append(entry)
                else:
                    char_pools.append(entry)
                    
        # The API already returns records sorted newest first. 
        # Our initial grouping reversed the elements manually when generating pity.
        # So we leave the array ordered as-is to let the newest banner top.
        
        if char_pools:
            render_data["poolGroups"].append({"label": "角色池", "pools": char_pools})
        if weapon_pools:
            render_data["poolGroups"].append({"label": "武器池", "pools": weapon_pools})
            
        try:
            img_url = await self.renderer.render_html("gacha/gacha-analysis.html", render_data)
            if img_url:
                yield event.image_result(img_url)
                return
        except Exception as e:
            logger.warning(f"渲染抽卡分析失败，使用文本回退: {e}")
            
        yield event.plain_result(f"【抽卡分析】总抽数：{render_data['totalCount']}（图片渲染失败）")

    @filter.command("wiki")
    async def wiki_search(self, event: AstrMessageEvent, name: str):
        '''查询Wiki百科'''
        keyword = name.split()[-1] if name.split() else name
        yield event.plain_result(f"正在查询 Wiki: {keyword}...")
        
        # Default to character search (sub_type_id=1)
        res = await self.client.get_wiki_search(keyword)
        if not res or "items" not in res:
            yield event.plain_result("查询 Wiki 失败。")
            return
            
        items = res["items"] if isinstance(res.get("items"), list) else []
        item = next((i for i in items if name in i.get("name", "")), None)
        if not item:
            yield event.plain_result(f"未找到关于 {name} 的 Wiki 条目。")
            return
            
        detail = await self.client.get_wiki_item_detail(item.get("id", item.get("item_id", "")))
        if not detail:
            yield event.plain_result(f"获取 {name} 详情失败。")
            return
            
        data = detail
        msg = f"【Wiki - {data.get('name')}】\n"
        if "caption" in data:
            msg += "".join([c.get("text", "") for c in data["caption"] if isinstance(c, dict) and c.get("kind") == "text"]) + "\n"
        
        # Simplified content rendering
        msg += "\n(详细内容请在游戏中查看或使用更完整的 Wiki)"
        
        if data.get("cover"):
            yield event.chain_result([
                Plain(msg),
                Image.fromURL(data["cover"])
            ])
        else:
            yield event.plain_result(msg)

    @filter.command("公告")
    async def announcement_cmd(self, event: AstrMessageEvent):
        '''获取公告列表 或 指定公告详情'''
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
                yield event.plain_result(f"找不到第 {index} 条公告（当前列表共 {len(list_data)} 条）")
                return
                
            list_item = list_data[index - 1]
            item_id = list_item.get("item_id")
            item = list_item
            if item_id:
                detail_res = await self.client.get_announcement_detail(str(item_id))
                if detail_res:
                    item = {**list_item, **detail_res}
                    
            render_data = build_detail_render_data(item)
            url = await self.renderer.render_html("announcement/detail.html", render_data)
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
                    "title": item.get('title') or "（未知标题）",
                    "timeStr": format_publish_time(item.get('published_at_ts')),
                    "coverUrl": get_cover_url(item)
                } for i, item in enumerate(list_data)
            ],
            "footerLine1": "由 AstrBot & Endfield Plugin 渲染",
            "pageWidth": 560
        }
        
        url = await self.renderer.render_html("announcement/list.html", render_data)
        if url:
            yield event.image_result(url)
        else:
            yield event.plain_result("渲染公告图片失败。")

    @filter.command("公告最新")
    async def announcement_latest(self, event: AstrMessageEvent):
        '''获取最新一条公告详情'''
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
        '''订阅公告推送（仅限群聊）'''
        if not event.get_group_id():
             yield event.plain_result("请在群聊中使用此命令。")
             return
             
        group_id = event.get_group_id()
        latest = await self.client.get_announcement_latest()
        ts = latest.get("published_at_ts", 0) if latest else 0
        await self.announce_mgr.add_subscription(group_id, ts)
        yield event.plain_result("已成功订阅公告推送！")

    @filter.command("取消订阅公告")
    async def unsubscribe_announcement(self, event: AstrMessageEvent):
        '''取消订阅公告推送'''
        if not event.get_group_id():
             yield event.plain_result("请在群聊中使用此命令。")
             return
        group_id = event.get_group_id()
        await self.announce_mgr.remove_subscription(group_id)
        yield event.plain_result("已取消公告订阅。")

    @filter.command("订阅理智")
    async def subscribe_sanity(self, event: AstrMessageEvent):
        '''订阅理智推送（满时提醒）'''
        if not event.get_group_id():
            yield event.plain_result("请在群聊中使用此命令，用于理智满时艾特您。")
            return
            
        user_id = event.get_sender_id()
        group_id = event.get_group_id()
        binding = await self.user_mgr.get_primary_binding(user_id)
        if not binding:
            yield event.plain_result("请先绑定森空岛账号后再订阅理智提醒。")
            return
            
        success = await self.sanity_mgr.add_subscription(user_id, group_id)
        if success:
            yield event.plain_result("已成功订阅理智满时提醒！")
        else:
            yield event.plain_result("您已经在该群聊中订阅过理智提醒。")

    @filter.command("取消订阅理智")
    async def unsubscribe_sanity(self, event: AstrMessageEvent):
        '''取消理智推送'''
        if not event.get_group_id():
            yield event.plain_result("请在群聊中使用此命令。")
            return
            
        user_id = event.get_sender_id()
        group_id = event.get_group_id()
        success = await self.sanity_mgr.remove_subscription(user_id, group_id)
        if success:
            yield event.plain_result("已取消理智订阅。")
        else:
            yield event.plain_result("您当前没有订阅过理智提醒。")

    @filter.command("日历")
    async def calendar_cmd(self, event: AstrMessageEvent):
        '''活动日历图'''
        res = await self.client.get_wiki_activities()
        
        # If res is None, the request failed (e.g., 401 Unauthorized or network error)
        if res is None:
            yield event.plain_result("获取活动列表失败，无法连接到终末地维基。请检查您的 API Key 是否已正确配置。")
            return
            
        # Ensure res is a list
        if not isinstance(res, list):
            if hasattr(res, 'get'):
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
            yield event.plain_result("获取到了活动列表，但当前列表为空（服务器暂无活动信息）。")
            return

        # Build data for rendering
        import datetime
        now = datetime.datetime.now()
        now_ts = int(now.timestamp())
        
        normal_acts = []
        perm_acts = []
        min_ts = float('inf')
        
        for act in res:
            try:
                st_ts = act.get("activity_start_at_ts") or act.get("start_at_ts")
                et_ts = act.get("activity_end_at_ts") or act.get("end_at_ts")
                
                if not st_ts or not et_ts:
                    continue
                    
                st_ts = int(st_ts)
                et_ts = int(et_ts)
                
                duration_days = (et_ts - st_ts) / 86400
                st = datetime.datetime.fromtimestamp(st_ts)
                et = datetime.datetime.fromtimestamp(et_ts)
                
                is_active = (st_ts <= now_ts <= et_ts)
                
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
                    "is_perm": is_perm
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
                    min_ts = min(min_ts, st_ts)
                    
            except Exception as e:
                logger.error(f"Error parsing activity time: {e}")
                
        if not normal_acts and not perm_acts:
            yield event.plain_result("暂无可解析的活动事件。")
            return
            
        if min_ts == float('inf'):
            if perm_acts:
                min_ts = min(a['st_ts'] for a in perm_acts)
            else:
                min_ts = now_ts
                
        # Calendar span: limit to 20~60 days
        max_normal_ts = max((a['et_ts'] for a in normal_acts), default=min_ts + 30 * 86400)
        total_duration = max_normal_ts - min_ts
        if total_duration < 20 * 86400:
            total_duration = 20 * 86400
        elif total_duration > 60 * 86400:
            total_duration = 60 * 86400
            
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
            
            if width_pct < 15:
                width_pct = 15
                if left_pct + width_pct > 100:
                    left_pct = 100 - width_pct
                    
            act["left_pct"] = left_pct
            act["width_pct"] = width_pct
            
            if 0 <= left_pct <= 100 and not act["is_perm"]:
                key_dates.add(act["st_ts"])
                
        # Pack into lanes
        lanes = []
        for act in normal_acts:
            placed = False
            for lane in lanes:
                last_act = lane[-1]
                # Assuming 0.5 days (43200s) padding minimum between events in the same lane
                if act["st_ts"] >= last_act["et_ts"] + 43200:
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
                if act["st_ts"] >= last_act["et_ts"] + 43200:
                    lane.append(act)
                    placed = True
                    break
            if not placed:
                perm_lanes.append([act])
                
        # Append perm lanes to normal lanes so they render at the bottom
        lanes.extend(perm_lanes)
                
        axis_dates = []
        for ds in sorted(list(key_dates)):
            dt = datetime.datetime.fromtimestamp(ds)
            axis_dates.append({
                "label": dt.strftime("%m.%d"),
                "left_pct": (ds - min_ts) / total_duration * 100
            })
            
        # Today's line
        now_pct = (now_ts - min_ts) / total_duration * 100
        now_line = None
        if 0 <= now_pct <= 100:
            now_line = {
                "label": "TODAY",
                "left_pct": now_pct
            }
        
        render_data = {
            "pluResPath": "file:///" + os.path.abspath(self.renderer.res_path).replace("\\", "/") + "/",
            "title": "版本日历",
            "lanes": lanes,
            "axis_dates": axis_dates,
            "now_line": now_line,
            "copyright": "AstrBot & Endfield Plugin",
            "pageWidth": 1200
        }
        
        url = await self.renderer.render_html("calendar/calendar.html", render_data)
        if url:
            yield event.image_result(url)
        else:
            yield event.plain_result("活动日历渲染失败。")


    async def announcement_task(self):
        '''后台公告推送任务'''
        while True:
            # 动态获取配置项，防止过短
            poll_interval_mins = int(self.config.get('announcement_poll_interval', 10))
            poll_interval_secs = max(60, poll_interval_mins * 60)
            await asyncio.sleep(poll_interval_secs)

            subs = await self.announce_mgr.get_subscriptions()
            if not subs:
                continue
                
            latest = await self.client.get_announcement_latest()
            if not latest or "published_at_ts" not in latest:
                continue
                
            ts = int(latest["published_at_ts"])
            for s in subs:
                if ts > int(s.get("since_ts", 0)):
                    # Push image instead of plain text if possible
                    item_id = latest.get("item_id")
                    item = latest
                    if item_id:
                        detail_res = await self.client.get_announcement_detail(str(item_id))
                        if detail_res: item = {**latest, **detail_res}
                        
                    render_data = build_detail_render_data(item)
                    url = await self.renderer.render_html("announcement/detail.html", render_data)
                    try:
                        if url:
                            await self.context.send_message(s["group_id"], [Image.fromFileSystem(url.replace("file:///", ""))])
                        else:
                            msg = f"【终末地新公告】\n{latest.get('title')}\n{latest.get('summary') or ''}"
                            await self.context.send_message(s["group_id"], [Plain(msg)])
                    except Exception as e:
                        logger.error(f"Failed to push announcement to {s['group_id']}: {e}")
                    await self.announce_mgr.update_since_ts(s["group_id"], ts)

    async def sanity_task(self):
        '''理智满值通知推送任务 (20分钟轮询)'''
        while True:
            await asyncio.sleep(20 * 60)
            subs = await self.sanity_mgr.get_subscriptions()
            if not subs:
                continue

            now_ts = int(time.time())
            
            for sub in subs:
                user_id = sub.get("user_id")
                group_id = sub.get("group_id")
                last_notified = sub.get("last_notified", 0)

                # Avoid notifying twice within 4 hours to prevent spam
                if now_ts - last_notified < 3600 * 4:
                    continue
                
                binding = await self.user_mgr.get_primary_binding(user_id)
                if not binding:
                    continue
                    
                token = binding.get("framework_token")
                role_id = binding.get("role_id")
                server_id = binding.get("server_id", 1)
                
                if not token or not role_id:
                    continue
                
                try:
                    stamina_data = await self.client.get_stamina(token, role_id, server_id)
                    if not stamina_data:
                        continue
                        
                    stamina_obj = stamina_data.get("stamina", {})
                    s_current = int(stamina_obj.get("current", 0) or 0)
                    s_max = int(stamina_obj.get("max", 0) or 0)
                    
                    if s_current > 0 and s_max > 0 and s_current >= s_max:
                        try:
                            msg = f"尊敬的干员管理员，您的理智已经达到上限（{s_current}/{s_max}），请及时清理。"
                            await self.context.send_message(group_id, [At(qq=user_id), Plain("【理智已满】\n"), Plain(msg)])
                            await self.sanity_mgr.update_last_notified(user_id, group_id, now_ts)
                        except Exception as e:
                            logger.error(f"Failed to push sanity notification: {e}")
                except Exception as e:
                    logger.error(f"Sanity task error for user {user_id}: {e}")
                    
                # Rate limit safety
                await asyncio.sleep(1.5)

    async def auto_sign_in_task(self):
        '''每日自动签到任务'''
        import datetime
        while True:
            try:
                # Calculate time until next sign-in
                now = datetime.datetime.now()
                target_time_str = self.auto_sign_in_time if hasattr(self, 'auto_sign_in_time') else "00:05"
                try:
                    target_hour, target_minute = map(int, target_time_str.split(':'))
                except ValueError:
                    target_hour, target_minute = 0, 5 # Default to 00:05 if format is invalid
                
                target_time = now.replace(hour=target_hour, minute=target_minute, second=0, microsecond=0)
                if now > target_time:
                    # If target time for today has passed, schedule for tomorrow
                    target_time += datetime.timedelta(days=1)
                
                wait_seconds = (target_time - now).total_seconds()
                logger.info(f"[Endfield Auto Sign-In] Next auto sign-in scheduled at {target_time.strftime('%Y-%m-%d %H:%M:%S')} (in {wait_seconds:.1f} seconds)")
                
                # Sleep until target time
                await asyncio.sleep(wait_seconds)
                
                # Wake up and sign in for all bound accounts
                all_bindings = await self.user_mgr.get_all_bindings()
                success_count = 0
                fail_count = 0
                
                for bind in all_bindings:
                    token = bind.get("framework_token")
                    role_id = bind.get("role_id")
                    if not token or not role_id:
                        continue
                        
                    try:
                        res = await self.client.get_attendance(token)
                        if res and isinstance(res, dict):
                            success_count += 1
                            logger.info(f"[Endfield Auto Sign-In] Success for role {role_id}")
                        else:
                            fail_count += 1
                            logger.warning(f"[Endfield Auto Sign-In] Failed format for role {role_id}: {res}")
                    except Exception as e:
                        fail_count += 1
                        logger.error(f"[Endfield Auto Sign-In] Error for role {role_id}: {e}")
                    
                    # Small delay between requests to avoid rate limits
                    await asyncio.sleep(1.5)
                    
                logger.info(f"[Endfield Auto Sign-In] Batch complete. Success: {success_count}, Failed/Skipped: {fail_count}")
                
            except asyncio.CancelledError:
                logger.info("[Endfield Auto Sign-In] Task cancelled.")
                break
            except Exception as e:
                logger.error(f"[Endfield Auto Sign-In] Unexpected error in task loop: {e}")
                await asyncio.sleep(60) # Sleep before retry on error to prevent hot-loop

    @filter.command("帝江号建设", alias=["帝江号"])
    async def spaceship_cmd(self, event: AstrMessageEvent):
        '''查询帝江号建设信息'''
        user_id = event.get_sender_id()
        bind = await self.user_mgr.get_primary_binding(user_id)
        if not bind:
            yield event.plain_result("您尚未绑定森空岛账号，请先使用 /授权登录 绑定。")
            return
            
        res = await self.client.get_spaceship(bind["framework_token"], bind["role_id"], int(bind.get("server_id", 1)))
        note = await self.client.get_note(bind["framework_token"], bind["role_id"], int(bind.get("server_id", 1)))
        
        char_avatar_map = {}
        user_avatar = ""
        if note:
            user_avatar = note.get("base", {}).get("avatarUrl", "")
            for c in note.get("chars", []):
                char_avatar_map[c.get("id")] = c.get("avatarSqUrl")

        if not res:
            yield event.plain_result("获取帝江号建设信息失败。")
            return
            
        # Data transformation for spaceship.html
        render_data = {
            "title": "帝江号建设汇报",
            "userNickname": bind.get("nickname", "未知"),
            "userLevel": note.get("base", {}).get("level", bind.get("level", 0)) if note else bind.get("level", 0),
            "userUid": bind.get("role_id", "0"),
            "userAvatar": user_avatar or res.get("role", {}).get("avatarUrl", ""),
            "roomCount": len(res.get("rooms", [])),
            "rooms": [],
            "pluResPath": "file:///" + os.path.abspath(self.renderer.res_path).replace("\\", "/") + "/"
        }
        
        # Build name-to-avatar mapping as fallback
        name_to_avatar = {c.get("name"): c.get("avatarSqUrl") for c in note.get("chars", [])} if note else {}
        
        import random
        for room in res.get("rooms", []):
            room_data = {
                "roomName": room.get("roomName", "未知房间"),
                "level": room.get("level", 1),
                "bgIndex": random.randint(1, 3), # 模板中用到 img/帝江号{{room.bgIndex}}.png
                "chars": []
            }
            for c in room.get("chars", []):
                char_id = c.get('charId')
                room_data["chars"].append({
                    "name": c.get("name", "未知"),
                    "avatar": char_avatar_map.get(char_id) or name_to_avatar.get(c.get("name"), ""),
                    "physicalStrength": round(c.get("physicalStrength", 0) / 100, 1),
                    "moodDisplay": f"{c.get('moodPercent', 0)}/100",
                    "favorability": int(c.get("favorability", 0)),
                    "trustLevelName": c.get("trustLevelName", ""),
                    "trustPercent": c.get("trustPercent", 0),
                    "trustDisplay": c.get("trustDisplay", "")
                })
            render_data["rooms"].append(room_data)
            
        render_data["copyright"] = "astrbot"
        
        url = await self.renderer.render_html("area/spaceship.html", render_data)
        if url:
            yield event.image_result(url)
        else:
            yield event.plain_result("渲染帝江号建设图片失败。")

    @filter.command("地区建设", alias=["建设"])
    async def area_cmd(self, event: AstrMessageEvent):
        '''查询地区建设信息'''
        user_id = event.get_sender_id()
        bind = await self.user_mgr.get_primary_binding(user_id)
        if not bind:
            yield event.plain_result("您尚未绑定森空岛账号，请先使用 /授权登录 绑定。")
            return
            
        res = await self.client.get_domain(bind["framework_token"], bind["role_id"], int(bind.get("server_id", 1)))
        note = await self.client.get_note(bind["framework_token"], bind["role_id"], int(bind.get("server_id", 1)))
        
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
            "userLevel": note.get("base", {}).get("level", bind.get("level", 0)) if note else bind.get("level", 0),
            "userUid": bind.get("role_id", "0"),
            "userAvatar": user_avatar, 
            "zoneCount": len(res.get("domain", [])),
            "zones": [],
            "pluResPath": "file:///" + os.path.abspath(self.renderer.res_path).replace("\\", "/") + "/"
        }
        
        # Build fallback maps from note (often more reliable for names/avatars)
        note_chars = note.get("chars", []) if note else []
        note_name_map = {c.get("id"): c.get("name") for c in note_chars}
        note_avatar_map = {c.get("id"): c.get("avatarSqUrl") for c in note_chars}
        
        # Merge with domain API's own map (if provided)
        # Using a unified map ensures we can resolve both short IDs (char_001) and long hashes
        char_name_map = {**note_name_map, **res.get("charNameMap", {})}
        char_avatar_map = note_avatar_map # note is the primary source for avatars
        
        # Build a name-to-avatar map as a last resort fallback
        name_to_avatar = {c.get("name"): c.get("avatarSqUrl") for c in note_chars}
        
        for domain in res.get("domain", []):
            zone_data = {
                "zoneName": domain.get("name", "未知地区"),
                "level": domain.get("level", 1),
                "totalChest": sum(c.get("trchestCount", 0) for c in domain.get("collections", [])),
                "totalPuzzle": sum(c.get("puzzleCount", 0) for c in domain.get("collections", [])),
                "totalBlackbox": sum(c.get("blackboxCount", 0) for c in domain.get("collections", [])),
                "settlements": []
            }
            for s in domain.get("settlements", []):
                # Try multiple possible field names for flexibility
                officer_ids = s.get("officerCharIds") or s.get("officerCharId") or s.get("officers") or ""
                
                if isinstance(officer_ids, list) and officer_ids:
                    officer_id = str(officer_ids[0])
                else:
                    officer_id = str(officer_ids) if officer_ids else ""
                
                # Resolve name and avatar with fallbacks
                officer_name = char_name_map.get(officer_id, "")
                officer_avatar = char_avatar_map.get(officer_id) or name_to_avatar.get(officer_name, "")
                    
                zone_data["settlements"].append({
                    "name": s.get("name", "未知聚落"),
                    "level": s.get("level", 1),
                    "officerName": officer_name,
                    "officerAvatar": officer_avatar
                })
            render_data["zones"].append(zone_data)
            
        render_data["copyright"] = "astrbot"
        
        url = await self.renderer.render_html("area/area.html", render_data)
        if url:
            yield event.image_result(url)
        else:
            yield event.plain_result("渲染地区建设图片失败。")


    async def terminate(self):
        if self._announcement_task_handle:
            self._announcement_task_handle.cancel()
        if self._sanity_task_handle:
            self._sanity_task_handle.cancel()
        if self._auto_sign_in_task_handle:
            self._auto_sign_in_task_handle.cancel()
        if self._http_client:
            await self._http_client.aclose()
        if self.client:
            await self.client.close()
        if self.renderer:
            await self.renderer.close()
        logger.info("Endfield plugin terminated.")
