from typing import Any

from services.firebase_client import get_firestore_client, get_firestore_module, run_firestore


COLLECTION_NAME = "deleted_image_cache"


class DeletedImageStore:
    def _collection_ref(self):
        return get_firestore_client().collection(COLLECTION_NAME)

    def _message_ref(self, message_id: int | str):
        return self._collection_ref().document(str(message_id))

    async def save_message(self, message_id: int | str, payload: dict[str, Any]) -> None:
        await run_firestore(self._save_message_sync, str(message_id), payload)

    def _save_message_sync(self, message_id: str, payload: dict[str, Any]) -> None:
        firestore = get_firestore_module()
        self._message_ref(message_id).set(
            {
                **payload,
                "updated_at": firestore.SERVER_TIMESTAMP,
            },
            merge=True,
        )

    async def load_message(self, message_id: int | str) -> dict[str, Any] | None:
        return await run_firestore(self._load_message_sync, str(message_id))

    def _load_message_sync(self, message_id: str) -> dict[str, Any] | None:
        snapshot = self._message_ref(message_id).get()
        if not snapshot.exists:
            return None
        return snapshot.to_dict() or {}

    async def delete_message(self, message_id: int | str) -> None:
        await run_firestore(self._delete_message_sync, str(message_id))

    def _delete_message_sync(self, message_id: str) -> None:
        self._message_ref(message_id).delete()

    async def delete_old_messages(self, cutoff, limit: int = 200) -> list[dict[str, Any]]:
        return await run_firestore(self._delete_old_messages_sync, cutoff, int(limit))

    def _delete_old_messages_sync(self, cutoff, limit: int) -> list[dict[str, Any]]:
        db = get_firestore_client()
        try:
            from google.cloud.firestore_v1.base_query import FieldFilter

            query = self._collection_ref().where(filter=FieldFilter("updated_at", "<=", cutoff))
        except ImportError:
            query = self._collection_ref().where("updated_at", "<=", cutoff)
        query = query.limit(max(1, int(limit)))
        snapshots = list(query.stream())
        if not snapshots:
            return []

        batch = db.batch()
        deleted_payloads: list[dict[str, Any]] = []
        for snapshot in snapshots:
            payload = snapshot.to_dict() or {}
            payload.setdefault("message_id", snapshot.id)
            deleted_payloads.append(payload)
            batch.delete(snapshot.reference)
        batch.commit()
        return deleted_payloads
