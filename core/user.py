import os
import json
from typing import List, Dict, Optional

class UserManager:
    def __init__(self, data_dir: str):
        self.data_dir = data_dir
        self.bindings_path = os.path.join(data_dir, "bindings.json")
        if not os.path.exists(data_dir):
            os.makedirs(data_dir)
        self.data = self._load()

    def _load(self) -> Dict[str, List[Dict]]:
        if os.path.exists(self.bindings_path):
            with open(self.bindings_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        return {}

    def _save(self):
        with open(self.bindings_path, 'w', encoding='utf-8') as f:
            json.dump(self.data, f, ensure_ascii=False, indent=4)

    def get_user_bindings(self, user_id: str) -> List[Dict]:
        return self.data.get(user_id, [])

    def get_primary_binding(self, user_id: str) -> Optional[Dict]:
        bindings = self.get_user_bindings(user_id)
        for b in bindings:
            if b.get("is_primary"):
                return b
        return bindings[0] if bindings else None

    def save_user_bindings(self, user_id: str, bindings: List[Dict]):
        # Normalize and clean bindings
        cleaned = []
        seen_role_ids = set()
        
        # Priority: primary first, then most recent last_sync
        sorted_bindings = sorted(bindings, key=lambda x: (x.get("is_primary", False), x.get("last_sync", 0)), reverse=True)
        
        for b in sorted_bindings:
            role_id = b.get("role_id")
            if role_id not in seen_role_ids:
                cleaned.append(b)
                seen_role_ids.add(role_id)
        
        # Ensure only one primary
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
        self._save()

    def delete_user_binding(self, user_id: str, binding_id: str):
        bindings = self.get_user_bindings(user_id)
        updated = [b for b in bindings if b.get("binding_id") != binding_id]
        if len(updated) < len(bindings):
            self.save_user_bindings(user_id, updated)
            return True
        return False

class SimulateManager:
    def __init__(self, data_dir: str):
        self.path = os.path.join(data_dir, "simulate_state.json")
        self.data = self._load()

    def _load(self):
        if os.path.exists(self.path):
            with open(self.path, 'r', encoding='utf-8') as f:
                return json.load(f)
        return {}

    def _save(self):
        with open(self.path, 'w', encoding='utf-8') as f:
            json.dump(self.data, f, ensure_ascii=False, indent=4)

    def get_state(self, scope: str, pool_type: str) -> Dict:
        if scope not in self.data:
            self.data[scope] = {}
        return self.data[scope].get(pool_type, {"gacha_history": [], "pity": 0})

    def save_state(self, scope: str, pool_type: str, state: Dict):
        if scope not in self.data:
            self.data[scope] = {}
        self.data[scope][pool_type] = state
        self._save()

class AnnouncementManager:
    def __init__(self, data_dir: str):
        self.path = os.path.join(data_dir, "announcements.json")
        self.data = self._load()

    def _load(self):
        if os.path.exists(self.path):
            with open(self.path, 'r', encoding='utf-8') as f:
                return json.load(f)
        return {"subscriptions": [], "last_ids": []}

    def _save(self):
        with open(self.path, 'w', encoding='utf-8') as f:
            json.dump(self.data, f, ensure_ascii=False, indent=4)

    def add_subscription(self, group_id: str, since_ts: int):
        for sub in self.data["subscriptions"]:
            if sub["group_id"] == group_id:
                sub["since_ts"] = since_ts
                self._save()
                return
        self.data["subscriptions"].append({"group_id": group_id, "since_ts": since_ts})
        self._save()

    def remove_subscription(self, group_id: str):
        self.data["subscriptions"] = [s for s in self.data["subscriptions"] if s["group_id"] != group_id]
        self._save()

    def get_subscriptions(self) -> List[Dict]:
        return self.data.get("subscriptions", [])

    def update_since_ts(self, group_id: str, ts: int):
        for sub in self.data["subscriptions"]:
            if sub["group_id"] == group_id:
                sub["since_ts"] = ts
                break
        self._save()

class MaaendManager:
    def __init__(self, data_dir: str):
        self.path = os.path.join(data_dir, "maaend.json")
        self.data = self._load()

    def _load(self):
        if os.path.exists(self.path):
            with open(self.path, 'r', encoding='utf-8') as f:
                return json.load(f)
        return {"users": {}, "groups": {}}

    def _save(self):
        with open(self.path, 'w', encoding='utf-8') as f:
            json.dump(self.data, f, ensure_ascii=False, indent=4)

    def get_user_devices(self, user_id: str) -> List[str]:
        return self.data["users"].get(user_id, {}).get("devices", [])

    def add_user_device(self, user_id: str, device_id: str):
        if user_id not in self.data["users"]:
            self.data["users"][user_id] = {"devices": [], "default_device": ""}
        if device_id not in self.data["users"][user_id]["devices"]:
            self.data["users"][user_id]["devices"].append(device_id)
            if not self.data["users"][user_id]["default_device"]:
                self.data["users"][user_id]["default_device"] = device_id
            self._save()

    def get_default_device(self, user_id: str) -> str:
        return self.data["users"].get(user_id, {}).get("default_device", "")

    def set_default_device(self, user_id: str, device_id: str):
        if user_id in self.data["users"] and device_id in self.data["users"][user_id]["devices"]:
            self.data["users"][user_id]["default_device"] = device_id
            self._save()
