import httpx
from astrbot.api import logger
from typing import Optional, Dict, Any, List

BASE_URL = "https://end-api.shallow.ink"
AUTH_FRONTEND_URL = "https://end.shallow.ink"


class EndfieldClient:
    def __init__(self, api_key: str = "", base_url: str = BASE_URL, verify_ssl: bool = True,
                 bot_qq: str = "", user_qq: str = ""):
        self.api_key = api_key
        self.base_url = base_url
        self.verify_ssl = verify_ssl
        self.bot_qq = str(bot_qq)
        self.user_qq = str(user_qq)
        self.client = httpx.AsyncClient(timeout=25.0, verify=self.verify_ssl)

    def set_caller(self, bot_qq: str = "", user_qq: str = ""):
        """Dynamically update bot/user QQ for per-request header context."""
        self.bot_qq = str(bot_qq)
        self.user_qq = str(user_qq)

    async def close(self):
        await self.client.aclose()

    def _headers(self, framework_token: Optional[str] = None) -> Dict[str, str]:
        h = {
            "Content-Type": "application/json",
            "platform": "bot",
        }
        if self.bot_qq:
            h["client_id"] = self.bot_qq
        if self.user_qq:
            h["platform_id"] = self.user_qq
        if self.api_key:
            h["X-API-Key"] = self.api_key
        if framework_token:
            h["X-Framework-Token"] = framework_token
        return h

    async def _get(self, path: str, params: Optional[Dict] = None, framework_token: Optional[str] = None) -> Optional[Any]:
        return await self._request("GET", path, params=params, framework_token=framework_token)

    async def _post(self, path: str, body: Optional[Dict] = None, framework_token: Optional[str] = None) -> Optional[Any]:
        return await self._request("POST", path, json_data=body, framework_token=framework_token)

    async def _delete(self, path: str, params: Optional[Dict] = None, framework_token: Optional[str] = None) -> Optional[Any]:
        return await self._request("DELETE", path, params=params, framework_token=framework_token)

    async def _request(self, method: str, path: str, params: Optional[Dict] = None,
                       json_data: Optional[Dict] = None, framework_token: Optional[str] = None) -> Optional[Any]:
        url = f"{self.base_url}{path}"
        headers = self._headers(framework_token)
        try:
            resp = await self.client.request(method, url, params=params, json=json_data, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            if not data:
                return None
            if data.get("code") == 0:
                return data.get("data", data)
            else:
                logger.warning(f"[Endfield API] {method} {path} -> code={data.get('code')}, msg={data.get('message', data.get('msg', ''))}")
                return None
        except httpx.HTTPStatusError as e:
            err_msg = e.response.text
            try:
                err_data = e.response.json()
                if isinstance(err_data, dict):
                    err_hint = err_data.get("message") or err_data.get("msg") or err_msg
                else:
                    err_hint = err_msg
            except:
                err_hint = err_msg
            logger.error(f"[Endfield API] {method} {path} -> HTTPError {e.response.status_code}: {err_hint}")
            return None
        except httpx.RequestError as e:
            logger.error(f"[Endfield API] {method} {path} -> Request Error {e}")
            return None
        except ValueError as e: # JSON decode error
            logger.error(f"[Endfield API] {method} {path} -> JSON Decode Error {e}")
            return None
        except Exception as e:
            logger.error(f"[Endfield API] {method} {path} -> Unknown Exception: {e}")
            return None

    # ─── Login ────────────────────────────────────────────────────────
    async def get_qr(self) -> Optional[Dict]:
        """GET /login/endfield/qr"""
        return await self._get("/login/endfield/qr")

    async def get_qr_status(self, framework_token: str) -> Optional[Dict]:
        """GET /login/endfield/qr/status"""
        return await self._get("/login/endfield/qr/status", params={"framework_token": framework_token})

    async def confirm_qr_login(self, framework_token: str, user_id: str) -> Optional[Dict]:
        """POST /login/endfield/qr/confirm"""
        return await self._post("/login/endfield/qr/confirm", body={
            "framework_token": framework_token,
            "user_identifier": user_id,
            "platform": "bot"
        })

    async def phone_send_code(self, phone: str) -> bool:
        """POST /login/endfield/phone/send"""
        res = await self._post("/login/endfield/phone/send", body={"phone": phone})
        return res is not None

    async def phone_verify_code(self, phone: str, code: str) -> Optional[Dict]:
        """POST /login/endfield/phone/verify"""
        return await self._post("/login/endfield/phone/verify", body={"phone": phone, "code": code})

    async def login_skport_password(self, email: str, password: str) -> Optional[Dict]:
        """POST /login/skport/password - International server login"""
        return await self._post("/login/skport/password", body={"email": email, "password": password})

    # ─── Authorization ────────────────────────────────────────────────
    async def create_authorization_request(self, client_id: str, client_name: str,
                                           client_type: str = "bot",
                                           scopes: List[str] = None) -> Optional[Dict]:
        """POST /api/v1/authorization/requests"""
        if scopes is None:
            scopes = ["user_info", "binding_info", "game_data", "attendance"]
        res = await self._post("/api/v1/authorization/requests", body={
            "client_id": client_id,
            "client_name": client_name,
            "client_type": client_type,
            "scopes": scopes
        })
        if res and res.get("auth_url", "").startswith("/"):
            res["auth_url"] = AUTH_FRONTEND_URL + res["auth_url"]
        return res

    async def get_authorization_request_status(self, request_id: str) -> Optional[Dict]:
        """GET /api/v1/authorization/requests/:id/status"""
        return await self._get(f"/api/v1/authorization/requests/{request_id}/status")

    # ─── Bindings ─────────────────────────────────────────────────────
    async def create_binding(self, framework_token: str, user_id: str, is_primary: bool = True,
                             provider: str = "skland", **kwargs) -> Optional[Dict]:
        """POST /api/v1/bindings"""
        body = {
            "framework_token": framework_token,
            "user_identifier": str(user_id),
            "client_type": "bot",
            "client_id": f"bot-{user_id}",
            "is_primary": is_primary,
            "provider": provider
        }
        body.update(kwargs)
        return await self._post("/api/v1/bindings", body=body)

    async def get_bindings(self, user_id: str, provider: str = "skland") -> List[Dict]:
        """GET /api/v1/bindings"""
        res = await self._get("/api/v1/bindings", params={"user_identifier": user_id, "client_type": "bot", "provider": provider})
        if res and "bindings" in res:
            return res["bindings"]
        return []

    async def delete_binding(self, binding_id: str, user_id: str) -> bool:
        """DELETE /api/v1/bindings/:id"""
        res = await self._delete(f"/api/v1/bindings/{binding_id}",
                                 params={"user_identifier": user_id, "client_type": "bot"})
        return res is not None

    async def set_primary_binding_by_id(self, binding_id: str) -> bool:
        """POST /api/v1/bindings/:id/primary"""
        res = await self._post(f"/api/v1/bindings/{binding_id}/primary")
        return res is not None

    # ─── Game Data (endfield) ─────────────────────────────────────────
    async def get_stamina(self, framework_token: str, role_id: str, server_id: int = 1) -> Optional[Dict]:
        """GET /api/endfield/stamina"""
        return await self._get("/api/endfield/stamina",
                               params={"roleId": role_id, "serverId": server_id},
                               framework_token=framework_token)

    async def get_note(self, framework_token: str, role_id: str, server_id: int = 1) -> Optional[Dict]:
        """GET /api/endfield/note"""
        return await self._get("/api/endfield/note",
                               params={"roleId": role_id, "serverId": server_id},
                               framework_token=framework_token)

    async def get_card_detail(self, framework_token: str, role_id: str, server_id: int = 1) -> Optional[Dict]:
        """GET /api/endfield/card/detail"""
        return await self._get("/api/endfield/card/detail",
                               params={"roleId": role_id, "serverId": server_id, "userId": role_id},
                               framework_token=framework_token)

    async def get_card_char(self, framework_token: str, inst_id: str = "",
                            operator_id: str = "", char_id: str = "",
                            role_id: str = "", server_id: int = 0) -> Optional[Dict]:
        """GET /api/endfield/card/char"""
        p: Dict[str, Any] = {}
        if inst_id:
            p["instId"] = inst_id
        else:
            if operator_id: p["operatorId"] = operator_id
            if char_id: p["charId"] = char_id
        if role_id: p["roleId"] = role_id
        if server_id: p["serverId"] = server_id
        return await self._get("/api/endfield/card/char", params=p, framework_token=framework_token)

    async def get_attendance(self, framework_token: str) -> Optional[Dict]:
        """POST /api/endfield/attendance"""
        return await self._post("/api/endfield/attendance", framework_token=framework_token)

    async def get_spaceship(self, framework_token: str, role_id: str = "", server_id: int = 1) -> Optional[Dict]:
        """GET /api/endfield/spaceship"""
        p = {"roleId": role_id, "serverId": server_id}
        return await self._get("/api/endfield/spaceship", params=p, framework_token=framework_token)

    async def get_domain(self, framework_token: str, role_id: str = "", server_id: int = 1) -> Optional[Dict]:
        """GET /api/endfield/domain"""
        p = {"roleId": role_id, "serverId": server_id}
        return await self._get("/api/endfield/domain", params=p, framework_token=framework_token)

    async def get_achieve(self, framework_token: str, role_id: str = "", server_id: int = 1) -> Optional[Dict]:
        """GET /api/endfield/achieve"""
        p = {"roleId": role_id, "serverId": server_id}
        return await self._get("/api/endfield/achieve", params=p, framework_token=framework_token)

    async def get_search_chars(self) -> Optional[Dict]:
        """GET /api/endfield/search/chars"""
        return await self._get("/api/endfield/search/chars")

    # ─── Friend (public, no framework_token needed) ───────────────────
    async def get_friend_detail(self, role_id: str) -> Optional[Dict]:
        """GET /api/friend/detail"""
        return await self._get("/api/friend/detail", params={"role_id": role_id})

    async def get_friend_char(self, role_id: str, template_id: str) -> Optional[Dict]:
        """GET /api/friend/char"""
        return await self._get("/api/friend/char", params={"role_id": role_id, "template_id": template_id})

    # ─── Gacha (all under /api/endfield/gacha/) ───────────────────────
    async def get_gacha_accounts(self, framework_token: str) -> Optional[Dict]:
        """GET /api/endfield/gacha/accounts"""
        return await self._get("/api/endfield/gacha/accounts", framework_token=framework_token)

    async def post_gacha_fetch(self, framework_token: str, role_id: str = "") -> Optional[Dict]:
        """POST /api/endfield/gacha/fetch — start sync"""
        body = {}
        if role_id:
            body["role_id"] = role_id
        return await self._post("/api/endfield/gacha/fetch", body=body, framework_token=framework_token)

    async def get_gacha_sync_status(self, framework_token: str) -> Optional[Dict]:
        """GET /api/endfield/gacha/sync/status"""
        return await self._get("/api/endfield/gacha/sync/status", framework_token=framework_token)

    async def get_gacha_records(self, framework_token: str, pools: str = "",
                                page: int = 1, limit: int = 20) -> Optional[Dict]:
        """GET /api/endfield/gacha/records"""
        p: Dict[str, Any] = {"page": page, "limit": limit}
        if pools:
            p["pools"] = pools
        return await self._get("/api/endfield/gacha/records", params=p, framework_token=framework_token)

    async def get_gacha_stats(self, framework_token: str) -> Optional[Dict]:
        """GET /api/endfield/gacha/stats"""
        return await self._get("/api/endfield/gacha/stats", framework_token=framework_token)

    async def get_gacha_global_stats(self, pool_period: str = "", provider: str = "") -> Optional[Dict]:
        """GET /api/endfield/gacha/global-stats"""
        p = {}
        if pool_period:
            p["pool_period"] = pool_period
        if provider:
            p["provider"] = provider
        return await self._get("/api/endfield/gacha/global-stats", params=p if p else None)

    async def get_gacha_pool_chars(self, pool_type: str = "") -> Optional[Dict]:
        """GET /api/endfield/gacha/pool-chars"""
        p = {}
        if pool_type:
            p["pool_type"] = pool_type
        return await self._get("/api/endfield/gacha/pool-chars", params=p if p else None)

    async def post_gacha_simulate_single(self, pool_type: str = "limited",
                                         state: Optional[Dict] = None) -> Optional[Dict]:
        """POST /api/endfield/gacha/simulate/single"""
        body: Dict[str, Any] = {"pool_type": pool_type}
        if state:
            body["state"] = state
        return await self._post("/api/endfield/gacha/simulate/single", body=body)

    async def post_gacha_simulate_ten(self, pool_type: str = "limited",
                                      state: Optional[Dict] = None) -> Optional[Dict]:
        """POST /api/endfield/gacha/simulate/ten"""
        body: Dict[str, Any] = {"pool_type": pool_type}
        if state:
            body["state"] = state
        return await self._post("/api/endfield/gacha/simulate/ten", body=body)

    # ─── Wiki (api_key only, no framework_token) ──────────────────────
    async def get_wiki_search(self, keyword: str, page: int = 1, page_size: int = 20) -> Optional[Dict]:
        """GET /api/wiki/search"""
        return await self._get("/api/wiki/search", params={"q": keyword, "page": page, "page_size": page_size})

    async def get_wiki_items(self, params: Dict) -> Optional[Dict]:
        """GET /api/wiki/items"""
        return await self._get("/api/wiki/items", params=params)

    async def get_wiki_item_detail(self, item_id: str) -> Optional[Dict]:
        """GET /api/wiki/items/:id"""
        return await self._get(f"/api/wiki/items/{item_id}")

    async def get_wiki_activities(self) -> Optional[Any]:
        """GET /api/wiki/activities"""
        return await self._get("/api/wiki/activities")

    # ─── Announcements (api_key only) ─────────────────────────────────
    async def get_announcements(self, page: int = 1, page_size: int = 20) -> Optional[Dict]:
        """GET /api/announcements"""
        return await self._get("/api/announcements", params={"page": page, "page_size": page_size})

    async def get_announcement_latest(self) -> Optional[Dict]:
        """GET /api/announcements/latest"""
        return await self._get("/api/announcements/latest")

    async def get_announcement_detail(self, item_id: str) -> Optional[Dict]:
        """GET /api/announcements/:id"""
        return await self._get(f"/api/announcements/{item_id}")

    # ─── MaaEnd ───────────────────────────────────────────────────────
    async def create_maaend_bind_code(self) -> Optional[Dict]:
        """POST /api/maaend/devices/bind-code"""
        return await self._post("/api/maaend/devices/bind-code")

    async def get_maaend_devices(self) -> Optional[Dict]:
        """GET /api/maaend/devices"""
        return await self._get("/api/maaend/devices")

    async def get_maaend_device_tasks(self, device_id: str) -> Optional[Dict]:
        """GET /api/maaend/devices/:id/tasks"""
        return await self._get(f"/api/maaend/devices/{device_id}/tasks")

    async def run_maaend_task(self, device_id: str, body: Dict) -> Optional[Dict]:
        """POST /api/maaend/devices/:id/tasks"""
        return await self._post(f"/api/maaend/devices/{device_id}/tasks", body=body)

    async def get_maaend_job(self, job_id: str) -> Optional[Dict]:
        """GET /api/maaend/jobs/:id"""
        return await self._get(f"/api/maaend/jobs/{job_id}")

    async def set_primary_binding(self, token: str, role_id: str, server_id: int) -> bool:
        """POST /api/enduid/set-primary"""
        res = await self._post("/api/enduid/set-primary", body={
            "role_id": role_id,
            "server_id": server_id
        }, framework_token=token)
        return res is not None

    async def stop_maaend_job(self, job_id: str) -> Optional[Dict]:
        """POST /api/maaend/jobs/:id/stop"""
        return await self._post(f"/api/maaend/jobs/{job_id}/stop")

    async def get_maaend_screenshot(self, device_id: str) -> Optional[bytes]:
        """GET /api/maaend/devices/:id/screenshot"""
        url = f"{self.base_url}/api/maaend/devices/{device_id}/screenshot"
        try:
            resp = await self.client.get(url, headers=self._headers())
            if resp.status_code == 200:
                return resp.content
            logger.error(f"[Endfield API] GET {url} -> HTTP {resp.status_code}")
        except Exception as e:
            logger.error(f"[Endfield API] GET {url} -> Exception: {e}")
        return None
    # ─── Panel Sync ───────────────────────────────────────────────────
    async def sync_panel(self, framework_token: str) -> Optional[Dict]:
        """POST /api/panel/sync"""
        return await self._post("/api/panel/sync", framework_token=framework_token)

    async def get_panel_sync_status(self, framework_token: str) -> Optional[Dict]:
        """GET /api/panel/sync/status"""
        return await self._get("/api/panel/sync/status", framework_token=framework_token)

    async def get_panel_chars(self, framework_token: str) -> Optional[Dict]:
        """GET /api/panel/chars"""
        return await self._get("/api/panel/chars", framework_token=framework_token)

    async def get_panel_char(self, framework_token: str, template_id: str) -> Optional[Dict]:
        """GET /api/panel/char/:template_id"""
        return await self._get(f"/api/panel/char/{template_id}", framework_token=framework_token)
