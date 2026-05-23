from typing import Any

from services.firebase_client import get_firestore_client, get_firestore_module, run_firestore


COLLECTION_NAME = "guard_configs"


def default_config() -> dict[str, Any]:
    return {
        "anti_ad_enabled": False,
    }


def normalize_config(config: dict[str, Any] | None) -> dict[str, Any]:
    normalized = default_config()
    if isinstance(config, dict):
        normalized["anti_ad_enabled"] = bool(config.get("anti_ad_enabled", False))
    return normalized


class GuardStore:
    def _config_ref(self, guild_id: int | str):
        return get_firestore_client().collection(COLLECTION_NAME).document(str(guild_id))

    async def load_all(self) -> dict[str, Any]:
        return await run_firestore(self._load_all_sync)

    def _load_all_sync(self) -> dict[str, Any]:
        data = {"version": 1, "guilds": {}}
        for config_doc in get_firestore_client().collection(COLLECTION_NAME).stream():
            data["guilds"][config_doc.id] = normalize_config(config_doc.to_dict() or {})
        return data

    async def save_guild(self, guild_id: int | str, config: dict[str, Any]) -> None:
        snapshot = normalize_config(config)
        await run_firestore(self._save_guild_sync, str(guild_id), snapshot)

    def _save_guild_sync(self, guild_id: str, config: dict[str, Any]) -> None:
        firestore = get_firestore_module()
        self._config_ref(guild_id).set(
            {
                "anti_ad_enabled": bool(config.get("anti_ad_enabled", False)),
                "updated_at": firestore.SERVER_TIMESTAMP,
            },
            merge=True,
        )
