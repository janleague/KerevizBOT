from copy import deepcopy
from typing import Any

from services.firebase_client import get_firestore_client, get_firestore_module, run_firestore


COLLECTION_NAME = "reaction_role_panels"


def normalize_panel(record: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(record, dict):
        return {}

    normalized = deepcopy(record)
    for key in ("guild_id", "channel_id", "message_id", "created_by_id"):
        value = normalized.get(key)
        normalized[key] = int(value) if value is not None else None

    normalized["status"] = str(normalized.get("status") or "active")
    normalized["panel_type"] = str(normalized.get("panel_type") or "notification_roles")
    return normalized


class ReactionRolePanelStore:
    def _collection_ref(self):
        return get_firestore_client().collection(COLLECTION_NAME)

    def _panel_ref(self, guild_id: int | str):
        return self._collection_ref().document(str(guild_id))

    async def load_all(self) -> dict[int, dict[str, Any]]:
        return await run_firestore(self._load_all_sync)

    def _load_all_sync(self) -> dict[int, dict[str, Any]]:
        panels: dict[int, dict[str, Any]] = {}
        for doc in self._collection_ref().stream():
            record = normalize_panel(doc.to_dict() or {})
            if record.get("guild_id") is None:
                try:
                    record["guild_id"] = int(doc.id)
                except ValueError:
                    continue
            panels[int(record["guild_id"])] = record
        return panels

    async def load_panel(self, guild_id: int | str) -> dict[str, Any]:
        return await run_firestore(self._load_panel_sync, str(guild_id))

    def _load_panel_sync(self, guild_id: str) -> dict[str, Any]:
        doc = self._panel_ref(guild_id).get()
        if not doc.exists:
            return {}
        record = normalize_panel(doc.to_dict() or {})
        record.setdefault("guild_id", int(guild_id))
        return record

    async def save_panel(self, record: dict[str, Any]) -> None:
        snapshot = normalize_panel(record)
        guild_id = snapshot.get("guild_id")
        if guild_id is None:
            raise ValueError("Reaction role panel record is missing a guild_id.")
        await run_firestore(self._save_panel_sync, str(guild_id), snapshot)

    def _save_panel_sync(self, guild_id: str, record: dict[str, Any]) -> None:
        firestore = get_firestore_module()
        self._panel_ref(guild_id).set(
            {
                **record,
                "version": 1,
                "updated_at": firestore.SERVER_TIMESTAMP,
            },
            merge=True,
        )
