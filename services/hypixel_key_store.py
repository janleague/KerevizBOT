from services.firebase_client import get_firestore_client, get_firestore_module, run_firestore


class HypixelAPIKeyStore:
    def _state_ref(self):
        return get_firestore_client().collection("bot_state").document("hypixel_api")

    async def load_api_key(self) -> str | None:
        return await run_firestore(self._load_api_key_sync)

    def _load_api_key_sync(self) -> str | None:
        snapshot = self._state_ref().get()
        if not snapshot.exists:
            return None
        value = (snapshot.to_dict() or {}).get("api_key")
        return str(value).strip() if value else None

    async def save_api_key(self, api_key: str, updated_by: int | str | None = None) -> None:
        clean_key = api_key.strip()
        if not clean_key:
            raise ValueError("Hypixel API key cannot be empty.")
        await run_firestore(self._save_api_key_sync, clean_key, updated_by)

    def _save_api_key_sync(self, api_key: str, updated_by: int | str | None = None) -> None:
        firestore = get_firestore_module()
        self._state_ref().set(
            {
                "api_key": api_key,
                "updated_by": str(updated_by) if updated_by is not None else None,
                "updated_at": firestore.SERVER_TIMESTAMP,
            },
            merge=True,
        )
