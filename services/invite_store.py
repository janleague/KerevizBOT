from copy import deepcopy
from typing import Any

from services.firebase_client import get_firestore_client, get_firestore_module, run_firestore


CONFIG_FIELDS = {
    "enabled",
    "count_leaves",
    "log_channel_id",
    "rewards",
    "vanity_uses",
    "last_sync_ts",
}


def default_config() -> dict[str, Any]:
    return {
        "enabled": False,
        "count_leaves": False,
        "log_channel_id": None,
        "rewards": [],
        "member_invites": {},
        "member_joins": {},
        "invite_cache": {},
        "vanity_uses": None,
        "last_sync_ts": 0,
    }


def normalize_config(config: dict[str, Any] | None) -> dict[str, Any]:
    normalized = default_config()
    if isinstance(config, dict):
        normalized.update({key: value for key, value in config.items() if key in normalized})
    normalized["member_invites"] = {
        str(user_id): int(total or 0)
        for user_id, total in (normalized.get("member_invites") or {}).items()
    }
    normalized["member_joins"] = {
        str(member_id): value
        for member_id, value in (normalized.get("member_joins") or {}).items()
    }
    normalized["invite_cache"] = {
        str(code): value
        for code, value in (normalized.get("invite_cache") or {}).items()
        if isinstance(value, dict)
    }
    normalized["rewards"] = sorted(
        [
            reward
            for reward in (normalized.get("rewards") or [])
            if isinstance(reward, dict) and "count" in reward and "role_id" in reward
        ],
        key=lambda reward: int(reward["count"]),
    )
    return normalized


class InviteTrackerStore:
    def _tracker_ref(self, guild_id: int | str):
        return get_firestore_client().collection("invite_trackers").document(str(guild_id))

    async def load_all(self) -> dict[str, Any]:
        return await run_firestore(self._load_all_sync)

    def _load_all_sync(self) -> dict[str, Any]:
        data = {"version": 1, "guilds": {}}
        for tracker_doc in get_firestore_client().collection("invite_trackers").stream():
            guild_id = tracker_doc.id
            config = normalize_config(tracker_doc.to_dict() or {})
            config["invite_cache"] = {
                doc.id: doc.to_dict() or {}
                for doc in tracker_doc.reference.collection("invite_cache").stream()
            }
            config["member_invites"] = {
                doc.id: int((doc.to_dict() or {}).get("total", 0))
                for doc in tracker_doc.reference.collection("member_invites").stream()
            }
            config["member_joins"] = {
                doc.id: (doc.to_dict() or {}).get("inviter_id")
                for doc in tracker_doc.reference.collection("member_joins").stream()
            }
            data["guilds"][guild_id] = normalize_config(config)
        return data

    async def save_guild(self, guild_id: int | str, config: dict[str, Any]) -> None:
        snapshot = normalize_config(deepcopy(config))
        await run_firestore(self._save_guild_sync, str(guild_id), snapshot)

    def _save_guild_sync(self, guild_id: str, config: dict[str, Any]) -> None:
        db = get_firestore_client()
        firestore = get_firestore_module()
        tracker_ref = self._tracker_ref(guild_id)

        tracker_ref.set(
            {
                **{key: config.get(key) for key in CONFIG_FIELDS},
                "version": 1,
                "updated_at": firestore.SERVER_TIMESTAMP,
            },
            merge=True,
        )
        self._replace_collection(
            db,
            tracker_ref.collection("invite_cache"),
            {
                code: {
                    "uses": int(data.get("uses", 0)),
                    "inviter_id": data.get("inviter_id"),
                    "channel_id": data.get("channel_id"),
                }
                for code, data in config.get("invite_cache", {}).items()
                if isinstance(data, dict)
            },
        )
        self._replace_collection(
            db,
            tracker_ref.collection("member_invites"),
            {
                user_id: {"total": int(total or 0)}
                for user_id, total in config.get("member_invites", {}).items()
            },
        )
        self._replace_collection(
            db,
            tracker_ref.collection("member_joins"),
            {
                member_id: {"inviter_id": inviter_id}
                for member_id, inviter_id in config.get("member_joins", {}).items()
            },
        )

    @staticmethod
    def _replace_collection(db, collection_ref, payload: dict[str, dict[str, Any]]) -> None:
        batch = db.batch()
        writes = 0

        def commit_if_needed(force: bool = False) -> None:
            nonlocal batch, writes
            if writes and (force or writes >= 450):
                batch.commit()
                batch = db.batch()
                writes = 0

        for snapshot in collection_ref.stream():
            batch.delete(snapshot.reference)
            writes += 1
            commit_if_needed()

        for document_id, data in payload.items():
            batch.set(collection_ref.document(str(document_id)), data)
            writes += 1
            commit_if_needed()

        commit_if_needed(force=True)
