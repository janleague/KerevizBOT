from typing import Any

from services.firebase_client import get_firestore_client, get_firestore_module, run_firestore


COLLECTION_NAME = "deleted_image_cache"


class DeletedImageStore:
    def _message_ref(self, message_id: int | str):
        return get_firestore_client().collection(COLLECTION_NAME).document(str(message_id))

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
