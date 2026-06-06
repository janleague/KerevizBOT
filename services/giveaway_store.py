from copy import deepcopy
from typing import Any

from services.firebase_client import get_firestore_client, get_firestore_module, run_firestore


COLLECTION_NAME = "giveaways"


def normalize_record(record: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(record, dict):
        return {}

    normalized = deepcopy(record)
    if normalized.get("id") is not None:
        normalized["id"] = str(normalized["id"])

    for key in (
        "guild_id",
        "channel_id",
        "message_id",
        "host_id",
        "created_by_id",
        "created_at",
        "ends_at",
        "ended_at",
        "winners_count",
        "required_role_id",
        "bonus_role_id",
        "bonus_entries",
        "color",
        "ping_role_id",
        "ended_by_id",
        "cancelled_by_id",
        "rerolled_at",
        "rerolled_by_id",
    ):
        value = normalized.get(key)
        normalized[key] = int(value) if value is not None else None

    normalized["winner_ids"] = [int(user_id) for user_id in normalized.get("winner_ids") or []]
    normalized["entrants"] = [int(user_id) for user_id in normalized.get("entrants") or []]
    normalized["winner_announcement_sent"] = bool(normalized.get("winner_announcement_sent", False))
    normalized["ping_everyone"] = bool(normalized.get("ping_everyone", False))
    normalized["status"] = str(normalized.get("status") or "active")
    return normalized


class GiveawayStore:
    def _collection_ref(self):
        return get_firestore_client().collection(COLLECTION_NAME)

    def _giveaway_ref(self, giveaway_id: int | str):
        return self._collection_ref().document(str(giveaway_id))

    async def load_all(self) -> dict[str, dict[str, Any]]:
        return await run_firestore(self._load_all_sync)

    def _load_all_sync(self) -> dict[str, dict[str, Any]]:
        giveaways: dict[str, dict[str, Any]] = {}
        for doc in self._collection_ref().stream():
            record = normalize_record(doc.to_dict() or {})
            record.setdefault("id", doc.id)
            giveaways[str(record["id"])] = record
        return giveaways

    async def save_giveaway(self, record: dict[str, Any]) -> None:
        snapshot = normalize_record(record)
        giveaway_id = snapshot.get("id")
        if not giveaway_id:
            raise ValueError("Giveaway record is missing an id.")
        await run_firestore(self._save_giveaway_sync, str(giveaway_id), snapshot)

    def _save_giveaway_sync(self, giveaway_id: str, record: dict[str, Any]) -> None:
        firestore = get_firestore_module()
        self._giveaway_ref(giveaway_id).set(
            {
                **record,
                "version": 1,
                "updated_at": firestore.SERVER_TIMESTAMP,
            },
            merge=True,
        )

    async def delete_giveaway(self, giveaway_id: int | str) -> None:
        await run_firestore(self._delete_giveaway_sync, str(giveaway_id))

    def _delete_giveaway_sync(self, giveaway_id: str) -> None:
        self._giveaway_ref(giveaway_id).delete()
