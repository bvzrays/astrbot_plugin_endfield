import os
import json
import asyncio
import copy
from typing import List, Dict, Optional, Any
from astrbot.api import logger


class AsyncDataManager:
    def __init__(self, data_dir: str, filename: str, default_data: Any):
        self.data_dir = data_dir
        self.path = os.path.join(data_dir, filename)
        self.default_data = default_data
        self.lock = asyncio.Lock()
        if not os.path.exists(data_dir):
            os.makedirs(data_dir, exist_ok=True)
        self.data = self._load()

    def _load(self) -> Any:
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"Failed to load {self.path}: {e}")
        return copy.deepcopy(self.default_data)

    async def _save(self):
        try:
            temp_path = self.path + ".tmp"
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=4)
            os.replace(temp_path, self.path)
        except Exception as e:
            logger.error(f"Failed to save {self.path}: {e}")

    async def get_all_data(self) -> Any:
        async with self.lock:
            return copy.deepcopy(self.data)


class UserManager(AsyncDataManager):
    def __init__(self, data_dir: str):
        super().__init__(data_dir, "bindings.json", {})

    async def get_user_bindings(self, user_id: Any) -> List[Dict]:
        user_id = str(user_id)
        async with self.lock:
            return copy.deepcopy(self.data.get(user_id, []))

    async def get_primary_binding(self, user_id: Any) -> Optional[Dict]:
        user_id = str(user_id)
        bindings = await self.get_user_bindings(user_id)
        for b in bindings:
            if b.get("is_primary"):
                return b
        return bindings[0] if bindings else None

    async def save_user_bindings(self, user_id: Any, bindings: List[Dict]):
        user_id = str(user_id)
        async with self.lock:
            # 规范化并清理绑定信息
            cleaned = []
            seen_role_ids = set()

            sorted_bindings = sorted(
                bindings,
                key=lambda x: (x.get("is_primary", False), x.get("last_sync", 0)),
                reverse=True,
            )

            for b in sorted_bindings:
                role_id = b.get("role_id")
                if role_id not in seen_role_ids:
                    cleaned.append(b)
                    seen_role_ids.add(role_id)

            if cleaned:
                has_primary = False
                for b in cleaned:
                    if b.get("is_primary"):
                        if has_primary:
                            b["is_primary"] = False
                        else:
                            has_primary = True
                if not has_primary:
                    cleaned[0]["is_primary"] = True

            self.data[user_id] = cleaned
            await self._save()

    async def delete_user_binding(self, user_id: Any, binding_id: str):
        user_id = str(user_id)
        bindings = await self.get_user_bindings(user_id)
        updated = [b for b in bindings if b.get("binding_id") != binding_id]
        if len(updated) < len(bindings):
            await self.save_user_bindings(user_id, updated)
            return True
        return False

    async def get_all_bindings(self) -> List[Dict]:
        async with self.lock:
            all_b = []
            for user_id, bindings in self.data.items():
                for b in bindings:
                    b_copy = copy.deepcopy(b)
                    b_copy["_user_id"] = user_id
                    all_b.append(b_copy)
            return all_b


class SimulateManager(AsyncDataManager):
    def __init__(self, data_dir: str):
        super().__init__(data_dir, "simulate_state.json", {})

    async def get_state(self, scope: str, pool_type: str) -> Dict:
        async with self.lock:
            if scope not in self.data:
                self.data[scope] = {}
                await self._save()
            return copy.deepcopy(
                self.data[scope].get(pool_type, {"gacha_history": [], "pity": 0})
            )

    async def save_state(self, scope: str, pool_type: str, state: Dict):
        async with self.lock:
            if scope not in self.data:
                self.data[scope] = {}
            self.data[scope][pool_type] = state
            await self._save()


class AnnouncementManager(AsyncDataManager):
    def __init__(self, data_dir: str):
        super().__init__(
            data_dir, "announcements.json", {"subscriptions": [], "last_ids": []}
        )

    async def add_subscription(
        self, group_id: str, since_ts: int, msg_origin: str = ""
    ):
        async with self.lock:
            for sub in self.data["subscriptions"]:
                if sub["group_id"] == group_id:
                    sub["since_ts"] = since_ts
                    sub["msg_origin"] = msg_origin
                    await self._save()
                    return
            self.data["subscriptions"].append(
                {"group_id": group_id, "since_ts": since_ts, "msg_origin": msg_origin}
            )
            await self._save()

    async def remove_subscription(self, group_id: str):
        async with self.lock:
            self.data["subscriptions"] = [
                s for s in self.data["subscriptions"] if s["group_id"] != group_id
            ]
            await self._save()

    async def get_subscriptions(self) -> List[Dict]:
        async with self.lock:
            return copy.deepcopy(self.data.get("subscriptions", []))

    async def update_since_ts(self, group_id: str, ts: int):
        async with self.lock:
            for sub in self.data["subscriptions"]:
                if sub["group_id"] == group_id:
                    sub["since_ts"] = ts
                    break
            await self._save()


class SanityManager(AsyncDataManager):
    def __init__(self, data_dir: str):
        super().__init__(data_dir, "sanity_subscriptions.json", {"subscriptions": []})

    async def add_subscription(self, user_id: str, msg_origin: str):
        """为用户添加或覆盖订阅（每个用户一个，以最新会话为准）。"""
        user_id = str(user_id)
        async with self.lock:
            for sub in self.data["subscriptions"]:
                if sub.get("user_id") == user_id:
                    sub["msg_origin"] = msg_origin
                    sub["last_notified"] = 0
                    await self._save()
                    return True
            self.data["subscriptions"].append(
                {"user_id": user_id, "msg_origin": msg_origin, "last_notified": 0}
            )
            await self._save()
            return True

    async def remove_subscription(self, user_id: str):
        user_id = str(user_id)
        async with self.lock:
            initial_len = len(self.data["subscriptions"])
            self.data["subscriptions"] = [
                s for s in self.data["subscriptions"] if s.get("user_id") != user_id
            ]
            if len(self.data["subscriptions"]) < initial_len:
                await self._save()
                return True
            return False

    async def get_subscriptions(self) -> List[Dict]:
        async with self.lock:
            return copy.deepcopy(self.data.get("subscriptions", []))

    async def update_last_notified(self, user_id: str, ts: int):
        user_id = str(user_id)
        async with self.lock:
            for sub in self.data["subscriptions"]:
                if sub.get("user_id") == user_id:
                    sub["last_notified"] = ts
                    break
            await self._save()


class MaaendManager(AsyncDataManager):
    def __init__(self, data_dir: str):
        super().__init__(data_dir, "maaend.json", {"users": {}, "groups": {}})

    async def get_user_devices(self, user_id: Any) -> List[str]:
        user_id = str(user_id)
        async with self.lock:
            return copy.deepcopy(self.data["users"].get(user_id, {}).get("devices", []))

    async def add_user_device(self, user_id: Any, device_id: str):
        user_id = str(user_id)
        async with self.lock:
            if user_id not in self.data["users"]:
                self.data["users"][user_id] = {"devices": [], "default_device": ""}
            if device_id not in self.data["users"][user_id]["devices"]:
                self.data["users"][user_id]["devices"].append(device_id)
                if not self.data["users"][user_id]["default_device"]:
                    self.data["users"][user_id]["default_device"] = device_id
                await self._save()

    async def get_default_device(self, user_id: Any) -> str:
        user_id = str(user_id)
        async with self.lock:
            return self.data["users"].get(user_id, {}).get("default_device", "")

    async def set_default_device(self, user_id: Any, device_id: str):
        user_id = str(user_id)
        async with self.lock:
            if (
                user_id in self.data["users"]
                and device_id in self.data["users"][user_id]["devices"]
            ):
                self.data["users"][user_id]["default_device"] = device_id
                await self._save()


class TicketManager(AsyncDataManager):
    def __init__(self, data_dir: str):
        super().__init__(data_dir, "ticket_subscriptions.json", {"subscriptions": []})

    async def add_subscription(self, user_id: str, msg_origin: str):
        user_id = str(user_id)
        async with self.lock:
            for sub in self.data["subscriptions"]:
                if sub.get("user_id") == user_id:
                    sub["msg_origin"] = msg_origin
                    sub["last_notified"] = 0
                    await self._save()
                    return True
            self.data["subscriptions"].append(
                {"user_id": user_id, "msg_origin": msg_origin, "last_notified": 0}
            )
            await self._save()
            return True

    async def remove_subscription(self, user_id: str):
        user_id = str(user_id)
        async with self.lock:
            initial_len = len(self.data["subscriptions"])
            self.data["subscriptions"] = [
                s for s in self.data["subscriptions"] if s.get("user_id") != user_id
            ]
            if len(self.data["subscriptions"]) < initial_len:
                await self._save()
                return True
            return False

    async def get_subscriptions(self) -> List[Dict]:
        async with self.lock:
            return copy.deepcopy(self.data.get("subscriptions", []))

    async def update_last_notified(self, user_id: str, ts: int):
        user_id = str(user_id)
        async with self.lock:
            for sub in self.data["subscriptions"]:
                if sub.get("user_id") == user_id:
                    sub["last_notified"] = ts
                    break
            await self._save()


class SignManager(AsyncDataManager):
    def __init__(self, data_dir: str):
        super().__init__(data_dir, "sign_state.json", {"last_sign_date": ""})

    async def get_last_sign_date(self) -> str:
        async with self.lock:
            return self.data.get("last_sign_date", "")

    async def set_last_sign_date(self, date_str: str):
        async with self.lock:
            self.data["last_sign_date"] = date_str
            await self._save()
