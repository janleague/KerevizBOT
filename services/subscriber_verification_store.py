from copy import deepcopy
from typing import Any

from services.firebase_client import get_firestore_client, get_firestore_module, run_firestore


REQUEST_COLLECTION = "subscriber_verifications"
PANEL_COLLECTION = "subscriber_verification_panels"


def normalize_request(record: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(record, dict):
        return {}

    normalized = deepcopy(record)
    if normalized.get("id") is not None:
        normalized["id"] = str(normalized["id"])

    for key in (
        "guild_id",
        "user_id",
        "created_at",
        "decided_at",
        "decided_by_id",
        "public_log_channel_id",
        "public_message_id",
        "review_channel_id",
        "review_message_id",
    ):
        value = normalized.get(key)
        normalized[key] = int(value) if value is not None else None

    normalized["status"] = str(normalized.get("status") or "pending")
    normalized["youtube_username"] = str(normalized.get("youtube_username") or "").strip()
    normalized["screenshot_url"] = str(normalized.get("screenshot_url") or "").strip()
    normalized["decision_reason"] = str(normalized.get("decision_reason") or "").strip()
    return normalized


def normalize_panel(record: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(record, dict):
        return {}

    normalized = deepcopy(record)
    for key in ("guild_id", "channel_id", "message_id", "created_by_id"):
        value = normalized.get(key)
        normalized[key] = int(value) if value is not None else None
    normalized["status"] = str(normalized.get("status") or "active")
    return normalized


class SubscriberVerificationStore:
    def _request_collection_ref(self):
        return get_firestore_client().collection(REQUEST_COLLECTION)

    def _panel_collection_ref(self):
        return get_firestore_client().collection(PANEL_COLLECTION)

    def _request_ref(self, request_id: int | str):
        return self._request_collection_ref().document(str(request_id))

    def _panel_ref(self, guild_id: int | str):
        return self._panel_collection_ref().document(str(guild_id))

    async def load_all_requests(self) -> dict[str, dict[str, Any]]:
        return await run_firestore(self._load_all_requests_sync)

    def _load_all_requests_sync(self) -> dict[str, dict[str, Any]]:
        requests: dict[str, dict[str, Any]] = {}
        for doc in self._request_collection_ref().stream():
            record = normalize_request(doc.to_dict() or {})
            record.setdefault("id", doc.id)
            requests[str(record["id"])] = record
        return requests

    async def save_request(self, record: dict[str, Any]) -> None:
        snapshot = normalize_request(record)
        request_id = snapshot.get("id")
        if not request_id:
            raise ValueError("Subscriber verification request is missing an id.")
        await run_firestore(self._save_request_sync, str(request_id), snapshot)

    def _save_request_sync(self, request_id: str, record: dict[str, Any]) -> None:
        firestore = get_firestore_module()
        self._request_ref(request_id).set(
            {
                **record,
                "version": 1,
                "updated_at": firestore.SERVER_TIMESTAMP,
            },
            merge=True,
        )

    async def delete_request(self, request_id: int | str) -> None:
        await run_firestore(self._delete_request_sync, str(request_id))

    def _delete_request_sync(self, request_id: str) -> None:
        self._request_ref(request_id).delete()

    async def load_all_panels(self) -> dict[int, dict[str, Any]]:
        return await run_firestore(self._load_all_panels_sync)

    def _load_all_panels_sync(self) -> dict[int, dict[str, Any]]:
        panels: dict[int, dict[str, Any]] = {}
        for doc in self._panel_collection_ref().stream():
            record = normalize_panel(doc.to_dict() or {})
            if record.get("guild_id") is None:
                try:
                    record["guild_id"] = int(doc.id)
                except ValueError:
                    continue
            panels[int(record["guild_id"])] = record
        return panels

    async def save_panel(self, record: dict[str, Any]) -> None:
        snapshot = normalize_panel(record)
        guild_id = snapshot.get("guild_id")
        if guild_id is None:
            raise ValueError("Subscriber verification panel is missing a guild_id.")
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
