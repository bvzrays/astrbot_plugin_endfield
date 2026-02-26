import asyncio
import os
import time
from astrbot.api import logger
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register, StarTools
from astrbot.core import AstrBotConfig
from astrbot.core.message.components import Plain, Image, At

from .core.client import EndfieldClient
from .core.user import UserManager, SimulateManager, AnnouncementManager, MaaendManager
from .core.utils import get_message
from .core.render import Renderer

@register("astrbot_plugin_endfield", "bvzrays", "终末地协议终端插件", "v1.0.0", "https://github.com/bvzrays/astrbot_plugin_endfield")
class EndfieldPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig = None):
        super().__init__(context)
        self.config = config or {}
        self.api_key = config.get("api_key", "") if config else ""
        self.client = EndfieldClient(self.api_key)
        
        data_dir = os.path.join(os.path.dirname(__file__), "data")
        if not os.path.exists(data_dir):
            os.makedirs(data_dir, exist_ok=True)
            
        self.user_mgr = UserManager(data_dir)
        self.sim_mgr = SimulateManager(data_dir)
        self.announce_mgr = AnnouncementManager(data_dir)
        self.maa_mgr = MaaendManager(data_dir)
        
        res_path = os.path.join(os.path.dirname(__file__), "resources")
        self.renderer = Renderer(res_path, self)

    def get_b64(self, rp):
        import mimetypes, base64
        if not rp:
            return ""
        if rp.startswith("http://") or rp.startswith("https://"):
            try:
                resp = httpx.get(rp, timeout=10)
                if resp.status_code == 200:
                    m = resp.headers.get("Content-Type", "image/png")
                    return f"data:{m};base64,{base64.b64encode(resp.content).decode()}"
            except Exception as e:
                logger.error(f"Failed to fetch external image {rp}: {e}")
            return rp
        
        fp = os.path.join(self.renderer.res_path, rp)
        if os.path.exists(fp):
            m = mimetypes.guess_type(fp)[0] or "image/png"
            with open(fp, "rb") as f:
                return f"data:{m};base64,{base64.b64encode(f.read()).decode()}"
        return ""

    async def initialize(self):
        # Announcement Task
        asyncio.create_task(self.announcement_task())

    @filter.command("zmd")
    async def zmd_help(self, event: AstrMessageEvent):
        '''显示终末地插件帮助菜单'''
        help_text = (
            "【终末地协议终端帮助】\n"
            "指令触发符：/ (默认) 或 :\n\n"
            "1. 账号绑定\n"
            "   :授权登陆 - 网页授权登录\n"
            "   :扫码绑定 - 扫码快捷登录\n"
            "   :手机绑定 [手机号] - 验证码登录\n"
            "   :绑定列表 - 查看已绑定账号\n"
            "   :切换绑定 [序号] - 切换当前账号\n"
            "   :删除绑定 [序号] - 删除绑定账号\n\n"
            "2. 信息查询\n"
            "   :便签 - 查询理智与日常活跃\n"
            "   :理智 - 快捷查询理智状态\n"
            "   :干员列表 - 查询干员列表\n"
            "   :<干员名>面板 - 查询干员详细面板\n\n"
            "3. 其他功能\n"
            "   :公告 - 查看最新官方公告\n"
            "   :wiki 干员 [名称] - 查询干员百科\n\n"
            "致谢原作者：QingYingX, 浅巷墨黎 (Entropy-Increase-Team)"
        )
        yield event.plain_result(help_text)

    @filter.command("帮助")
    async def help_alias(self, event: AstrMessageEvent):
        '''显示帮助的别名'''
        async for result in self.zmd_help(event):
            yield result

    @filter.command("绑定列表")
    async def bind_list(self, event: AstrMessageEvent):
        '''查看已绑定账号'''
        user_id = event.get_sender_id()
        bindings = self.user_mgr.get_user_bindings(user_id)
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
        bindings = self.user_mgr.get_user_bindings(user_id)
        if not (1 <= index <= len(bindings)):
            yield event.plain_result(f"序号错误，请选择 1 到 {len(bindings)} 之间的数字。")
            return
            
        for i, b in enumerate(bindings):
            b["is_primary"] = (i + 1 == index)
            
        self.user_mgr.save_user_bindings(user_id, bindings)
        yield event.plain_result(f"已切换至账号：{bindings[index-1]['nickname']}")
        async for res in self.bind_list(event):
            yield res

    @filter.command("删除绑定")
    async def delete_bind(self, event: AstrMessageEvent, index: int):
        '''删除指定绑定账号'''
        user_id = event.get_sender_id()
        bindings = self.user_mgr.get_user_bindings(user_id)
        if not (1 <= index <= len(bindings)):
            yield event.plain_result(f"序号错误，请选择 1 到 {len(bindings)} 之间的数字。")
            return
            
        target = bindings[index-1]
        confirm = await self.client.delete_binding(target["binding_id"], user_id)
        
        self.user_mgr.delete_user_binding(user_id, target["binding_id"])
        
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
            "bind_time": int(asyncio.get_event_loop().time() * 1000) # Simple TS
        }
        
        existing = self.user_mgr.get_user_bindings(user_id)
        existing.append(new_account)
        self.user_mgr.save_user_bindings(user_id, existing)
        
        yield event.plain_result(get_message("enduid.login_ok", {
            "nickname": new_account["nickname"],
            "role_id": new_account["role_id"],
            "server_id": "官服" if new_account["server_id"] == 1 else "B服",
            "count": len(self.user_mgr.get_user_bindings(user_id))
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
        import tempfile
        img_data = base64.b64decode(qr_b64.split(",")[-1])
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            tmp.write(img_data)
            tmp_path = tmp.name
            
        from astrbot.api.message_components import Image
        yield event.chain_result([
            Image.fromFileSystem(tmp_path),
            Plain("请使用森空岛 APP 扫描二维码进行登录。\n二维码有效时间约 3 分钟。")
        ])
        
        # Polling
        max_attempts = 90
        login_data = None
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
                os.remove(tmp_path)
                return
        
        os.remove(tmp_path)
        if not login_data:
            yield event.plain_result("登录超时。")
            return
            
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
            "bind_time": int(asyncio.get_event_loop().time() * 1000)
        }
        existing = self.user_mgr.get_user_bindings(user_id)
        existing.append(acc)
        self.user_mgr.save_user_bindings(user_id, existing)
        
        yield event.plain_result(get_message("enduid.login_ok", {
            "nickname": acc["nickname"],
            "role_id": acc["role_id"],
            "server_id": "官服" if acc["server_id"] == 1 else "B服",
            "count": len(self.user_mgr.get_user_bindings(user_id))
        }))

    @filter.command("手机绑定")
    async def phone_login(self, event: AstrMessageEvent, phone: str):
        '''手机号验证码登录'''
        if not event.is_private():
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
                "bind_time": int(asyncio.get_event_loop().time() * 1000)
            }
            existing = self.user_mgr.get_user_bindings(event.get_sender_id())
            existing.append(acc)
            self.user_mgr.save_user_bindings(event.get_sender_id(), existing)
            
            await waiter_event.send(get_message("enduid.login_ok", {
                "nickname": acc["nickname"],
                "role_id": acc["role_id"],
                "server_id": "官服" if acc["server_id"] == 1 else "B服",
                "count": len(self.user_mgr.get_user_bindings(event.get_sender_id()))
            }))
            controller.stop()
            
        try:
            await waiter(event)
        except TimeoutError:
            yield event.plain_result("验证超时。")
    @filter.command("便签")
    async def stamina(self, event: AstrMessageEvent):
        '''查询理智状态'''
        user_id = event.get_sender_id()
        binding = self.user_mgr.get_primary_binding(user_id)
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
        
        # Structure data for the template (mimicking stamina.js logic)
        # API returns: {stamina: {current, max, maxTs, recover}, dailyMission: {activation, maxActivation}, role: {name, level, roleId}}
        stamina_obj = stamina_data.get("stamina", {})
        daily_obj = stamina_data.get("dailyMission", {})
        role_obj = stamina_data.get("role", {})
        
        s_current = int(stamina_obj.get("current", 0) or 0)
        s_max = int(stamina_obj.get("max", 0) or 0)
        s_maxTs = int(stamina_obj.get("maxTs", 0) or 0)
        s_recover = int(stamina_obj.get("recover", 360) or 360)
        a_current = int(daily_obj.get("activation", 0) or 0)
        a_max = int(daily_obj.get("maxActivation", 100) or 100)
        
        # Calculate full time
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
        
        # Prefer note data for user info if available
        user_name = binding.get('nickname')
        user_level = 0
        if note_data:
            base = note_data.get("base", {})
            user_name = base.get("name") or role_obj.get("name") or user_name
            user_level = int(base.get("level", 0) or role_obj.get("level", 0) or 0)
        else:
            user_name = role_obj.get("name") or user_name
            user_level = int(role_obj.get("level", 0) or 0)
        
        acc_data = {
            "userName": user_name,
            "userUid": role_id,
            "userLevel": user_level,
            "current": s_current,
            "max": s_max,
            "staminaPercent": (s_current / max(s_max, 1)) * 100,
            "fullTime": full_time,
            "activation": a_current,
            "maxActivation": a_max,
            "activationPercent": (a_current / max(a_max, 1)) * 100,
        }
        
        # Find operator image
        op_dir = os.path.join(self.renderer.res_path, "img", "operator")
        if os.path.exists(op_dir):
            ops = [f for f in os.listdir(op_dir) if f.endswith(('.png', '.jpg', '.webp'))]
            if ops:
                import random
                op_file = random.choice(ops)
                # Read operator img directly into base64 as well to avoid path issues
                acc_data["operatorImg"] = self.get_b64(os.path.join("img", "operator", op_file))
        
        acc_data["staminaBgImg"] = self.get_b64("img/stbg.png")

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
            logger.warning(f"渲染便笾失败，使用文本回退: {e}")
        yield event.plain_result(f"【{acc_data['userName']}】\n理智：{acc_data['current']}/{acc_data['max']}\n活跃度：{acc_data['activation']}/{acc_data['maxActivation']}\n回满时间：{acc_data['fullTime']}")

    @filter.command("签到")
    async def attendance(self, event: AstrMessageEvent):
        '''每日签到'''
        user_id = event.get_sender_id()
        all_bindings = self.user_mgr.get_user_bindings(user_id)
        if not all_bindings:
            yield event.plain_result("未绑定账号。")
            return
            
        results = []
        for b in all_bindings:
            label = b.get("nickname") or b.get("role_id")
            token = b.get("framework_token")
            res = await self.client.get_attendance(token)
            if not res:
                results.append(f"【{label}】签到失败或今日已签到。")
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
        binding = self.user_mgr.get_primary_binding(user_id)
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

        operators = []
        for c in chars:
            char_data = c.get("charData", c)
            prof = char_data.get("profession", {}).get("value", "")
            prop = char_data.get("property", {}).get("value", "")
            operators.append({
                "name": char_data.get("name", "未知"),
                "nameChars": list(char_data.get("name", "未知")),
                "rarity": int(char_data.get("rarity", {}).get("value", 1)) if isinstance(char_data.get("rarity"), dict) else 1,
                "level": c.get("level", 0),
                "imageUrl": char_data.get("avatarRtUrl", ""),
                "profession": prof,
                "property": prop,
                "professionIcon": self.get_b64(f"meta/class/{prof}.jpg") if prof else "",
                "propertyIcon": self.get_b64(f"meta/attrpanle/{prop}.jpg") if prop else "",
                "phaseIcon": self.get_b64(f"meta/phases/phase-{c.get('evolvePhase', 0)}.png"),
                "potentialLevel": c.get('potentialLevel', 0),
                "colorCode": color_codes.get(prop, "PHY")
            })
            
        operators.sort(key=lambda x: (x["rarity"], x["level"]), reverse=True)
        
        # Calculate layout constraints
        list_card_width = 175
        list_column_count = 6
        list_gap_px = 12
        list_content_width = (list_card_width * list_column_count) + (list_gap_px * (list_column_count - 1))
        list_page_width = list_content_width + 56 # 28px padding on each side
        
        list_bg_file = "opbg.png"
        
        render_data = {
            "totalCount": len(operators),
            "operators": operators,
            "userNickname": detail.get("base", {}).get("name", binding.get("nickname")),
            "userLevel": detail.get("base", {}).get("level", 0),
            "userAvatar": self.get_b64(binding.get("avatarUrl", "")),
            "listBgFile": self.get_b64(f"operator/img/{list_bg_file}"),
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

    @filter.regex(r"^(?:[:：]|[/#](?:zmd|终末地))(.+?)面板$")
    async def operator_panel(self, event: AstrMessageEvent, char_name: str):
        '''查询干员详细面板'''
        user_id = event.get_sender_id()
        binding = self.user_mgr.get_primary_binding(user_id)
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
        binding = self.user_mgr.get_primary_binding(user_id)
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
            "userAvatar": self.get_b64(binding.get("avatarUrl", "")),
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
        binding = self.user_mgr.get_primary_binding(user_id)
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
        binding = self.user_mgr.get_primary_binding(user_id)
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
        
        render_data = {
            "title": "抽卡分析",
            "subtitle": "个人数据",
            "totalCount": stats.get("total_count", 0),
            "star6": stats.get("star6_count", 0),
            "star5": stats.get("star5_count", 0),
            "star4": stats.get("star4_count", 0),
            "userNickname": binding.get('nickname') or "未知",
            "userUid": binding.get("role_id", ""),
            "userAvatar": self.get_b64(binding.get("avatarUrl", "")),
            "analysisTime": analysisTime,
            "poolGroups": [],
            "copyright": "Endfield Plugin | AstrBot"
        }
        
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
            for r in records:
                pool_name = r.get("pool_name", "未知")
                if pool_name not in pools_dict:
                    pools_dict[pool_name] = []
                pools_dict[pool_name].append(r)
                
            for pool_name, pool_records in pools_dict.items():
                # Sort asc to calculate pity
                pool_records.sort(key=lambda x: str(x.get("seq_id", "")), reverse=False)
                
                total = len(pool_records)
                star6_count = 0
                images = []
                pity_since_last = 0
                
                for r in pool_records:
                    pity_since_last += 1
                    if r.get("rarity") == 6:
                        star6_count += 1
                        images.append({
                            "name": r.get("char_name") or r.get("item_name"),
                            "pullCount": pity_since_last,
                            "tag": "UP" if "UP" in str(r.get("item_name", "")) else "6星",
                            "badgeColor": "up" if "UP" in str(r.get("item_name", "")) else "normal",
                            "barPercent": min(100, int((pity_since_last / 80) * 100)),
                            "barColorLevel": "green" if pity_since_last < 50 else "yellow" if pity_since_last < 80 else "red"
                        })
                        pity_since_last = 0
                        
                # Reverse images for display (newest first)
                images.reverse()
                
                entry = {
                    "poolName": pool_name,
                    "total": total,
                    "star6": star6_count,
                    "metric1Label": "平均花费",
                    "metric1": f"{total // star6_count}抽" if star6_count > 0 else "-",
                    "metric2Label": "未出红",
                    "metric2": f"{pity_since_last}抽",
                    "images": images,
                    "pitySinceLast6": pity_since_last,
                    "pityBarPercent": min(100, int((pity_since_last / 80) * 100)),
                    "pityBarColorLevel": "green" if pity_since_last < 50 else "yellow" if pity_since_last < 80 else "red",
                    "freeTotal": 0
                }
                
                if key == "weapon":
                    weapon_pools.append(entry)
                else:
                    char_pools.append(entry)
                    
        # Reverse to show newest banner first
        char_pools.reverse()
        weapon_pools.reverse()
        
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
        yield event.plain_result(f"正在查询 Wiki: {name}...")
        
        # Default to character search (sub_type_id=1)
        res = await self.client.get_wiki_search(name)
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

    @filter.regex(r"^(?:[:：]|[/#](?:zmd|终末地))(十连|百连|单抽)(?:\s*[（(]?(常驻|UP|武器|限定)[）)]?)?\s*$")
    async def gacha_simulate(self, event: AstrMessageEvent, cmd: str, pool_name: str = None):
        '''模拟抽卡'''
        pool_type_map = {"常驻": "standard", "UP": "limited", "限定": "limited", "武器": "weapon"}
        pool_type = pool_type_map.get(pool_name, "limited")
        
        user_id = event.get_sender_id()
        scope = f"user_{user_id}"
        state = self.sim_mgr.get_state(scope, pool_type)
        
        if cmd == "单抽":
            res = await self.client.post_gacha_simulate_single(pool_type, state)
            if res and "result" in res:
                self.sim_mgr.save_state(scope, pool_type, res.get("state"))
                r = res["result"]
                msg = f"【模拟单抽 - {pool_name or 'UP池'}】\n★{r.get('rarity')} {r.get('name') or ''}"
                yield event.plain_result(msg)
        elif cmd == "十连":
            res = await self.client.post_gacha_simulate_ten(pool_type, state)
            if res and "results" in res:
                self.sim_mgr.save_state(scope, pool_type, res.get("state"))
                msg = f"【模拟十连 - {pool_name or 'UP池'}】\n"
                for r in res["results"]:
                    msg += f"★{r.get('rarity')} {r.get('name') or ''}\n"
                yield event.plain_result(msg)
        elif cmd == "百连":
             # Similar to ten but repeat 10 times if needed or just one call if backend supports
             yield event.plain_result("百连模拟正在开发中...")

    @filter.command("公告")
    async def announcement_list(self, event: AstrMessageEvent):
        '''获取公告列表'''
        res = await self.client.get_announcements(1, 5)
        if not res or "data" not in res:
            yield event.plain_result("获取公告失败。")
            return
            
        list_data = res["data"].get("list", [])
        if not list_data:
            yield event.plain_result("暂无公告。")
            return
            
        msg = "【最近公告】\n"
        for i, item in enumerate(list_data):
            msg += f"{i+1}. {item.get('title')}\n"
        yield event.plain_result(msg)

    @filter.command("订阅公告")
    async def subscribe_announcement(self, event: AstrMessageEvent):
        '''订阅公告推送（仅限群聊）'''
        if not event.is_group():
             yield event.plain_result("请在群聊中使用此命令。")
             return
             
        group_id = event.get_group_id()
        latest = await self.client.request("GET", "/api/announcements/latest")
        ts = latest.get("published_at_ts", 0) if latest else 0
        self.announce_mgr.add_subscription(group_id, ts)
        yield event.plain_result("已成功订阅公告推送！")

    @filter.command("取消订阅公告")
    async def unsubscribe_announcement(self, event: AstrMessageEvent):
        '''取消订阅公告推送'''
        if not event.is_group():
             yield event.plain_result("请在群聊中使用此命令。")
             return
        group_id = event.get_group_id()
        self.announce_mgr.remove_subscription(group_id)
        yield event.plain_result("已取消公告订阅。")

    async def announcement_task(self):
        '''后台公告推送任务'''
        while True:
            await asyncio.sleep(600) # Check every 10 mins
            subs = self.announce_mgr.get_subscriptions()
            if not subs:
                continue
                
            latest = await self.client.request("GET", "/api/announcements/latest")
            if not latest or "published_at_ts" not in latest:
                continue
                
            ts = int(latest["published_at_ts"])
            for s in subs:
                if ts > int(s.get("since_ts", 0)):
                    # Push!
                    msg = f"【终末地新公告】\n{latest.get('title')}\n{latest.get('summary') or ''}"
                    # How to push to group in AstrBot?
                    # Note: Need to get group object from context
                    # For now, just update TS to avoid repeated checks
                    self.announce_mgr.update_since_ts(s["group_id"], ts)

    @filter.command("理智")
    async def stamina_alias(self, event: AstrMessageEvent):
        '''理智命令别名'''
        async for result in self.stamina(event):
            yield result

    @filter.command("maa设备")
    async def maa_device(self, event: AstrMessageEvent):
        '''查看MaaEnd设备列表'''
        user_id = event.get_sender_id()
        device_ids = self.maa_mgr.get_user_devices(user_id)
        res = await self.client.get_maaend_devices()
        if not res or "devices" not in res.get("data", {}):
            yield event.plain_result("获取设备列表失败。")
            return
            
        all_devices = res["data"]["devices"]
        user_devices = [d for d in all_devices if d.get("device_id") in device_ids]
        
        if not user_devices:
            yield event.plain_result("尚未绑定任何设备。请使用 :maa绑定 获取绑定码。")
            return
            
        msg = "【我的 MaaEnd 设备】\n"
        for i, d in enumerate(user_devices):
            msg += f"{i+1}. {d.get('device_name') or d.get('device_id')} [{d.get('status')}]\n"
        yield event.plain_result(msg)

    @filter.command("maa绑定")
    async def maa_bind(self, event: AstrMessageEvent):
        '''获取MaaEnd绑定码'''
        res = await self.client.create_maaend_bind_code()
        if not res or "data" not in res:
             yield event.plain_result("生成绑定码失败。")
             return
             
        data = res["data"]
        msg = f"【MaaEnd 绑定码】\n绑定码：{data.get('bind_code')}\n请在 MaaEnd Client 中输入此动态码完成绑定。"
        yield event.plain_result(msg)

    @filter.command("maa截图")
    async def maa_screenshot(self, event: AstrMessageEvent):
        '''截取默认设备屏幕'''
        user_id = event.get_sender_id()
        device_id = self.maa_mgr.get_default_device(user_id)
        if not device_id:
            yield event.plain_result("未设置默认设备。")
            return
            
        yield event.plain_result("正在请求截图...")
        res = await self.client.get_maaend_screenshot(device_id)
        if res and isinstance(res, bytes):
            # Save temporary image or send raw bytes?
            # AstrBot usually takes URL or path.
            # For now, just send a success message or implement better image handling
            yield event.plain_result("截图成功（功能开发中，暂不显示图片）。")
        else:
            yield event.plain_result("截图失败。")

    async def terminate(self):
        await self.client.close()
        logger.info("Endfield plugin terminated.")
