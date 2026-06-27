import os
import time
from datetime import datetime, timezone

from services.firebase_client import get_firestore_client, get_firestore_module, run_firestore


class YouTubeAnnouncementStore:
    # Once a send attempt is claimed, automatic retries are blocked to avoid duplicate @everyone pings.
    FINAL_STATUSES = {"pending", "sent", "manually_acknowledged", "failed", "send_failed"}

    def __init__(self, stale_seconds: int = 300):
        self.stale_seconds = stale_seconds

    @property
    def storage_label(self) -> str:
        return "Firebase Firestore"

    def _state_ref(self):
        return get_firestore_client().collection("bot_state").document("youtube")

    def _announcement_ref(self, video_id: str):
        return get_firestore_client().collection("youtube_announcements").document(video_id)

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    def _pending_claim_is_stale(self, claimed_at) -> bool:
        if not isinstance(claimed_at, datetime):
            return True
        if claimed_at.tzinfo is None:
            claimed_at = claimed_at.replace(tzinfo=timezone.utc)
        return (self._now() - claimed_at).total_seconds() >= self.stale_seconds

    async def load_last_video_id(self) -> str | None:
        return await run_firestore(self._load_last_video_id_sync)

    def _load_last_video_id_sync(self) -> str | None:
        snapshot = self._state_ref().get()
        if not snapshot.exists:
            return None
        value = (snapshot.to_dict() or {}).get("last_video_id")
        return str(value).strip() if value else None

    async def set_last_video_id(self, video_id: str) -> None:
        await run_firestore(self._set_last_video_id_sync, video_id)

    def _set_last_video_id_sync(self, video_id: str) -> None:
        firestore = get_firestore_module()
        self._state_ref().set(
            {
                "last_video_id": video_id,
                "updated_at": firestore.SERVER_TIMESTAMP,
            },
            merge=True,
        )

    async def acknowledge_video(
        self,
        video_id: str,
        channel_id: int | None = None,
        actor_id: int | None = None,
    ) -> None:
        await run_firestore(self._acknowledge_video_sync, video_id, channel_id, actor_id)

    def _acknowledge_video_sync(
        self,
        video_id: str,
        channel_id: int | None = None,
        actor_id: int | None = None,
    ) -> None:
        db = get_firestore_client()
        firestore = get_firestore_module()
        batch = db.batch()
        batch.set(
            self._state_ref(),
            {
                "last_video_id": video_id,
                "updated_at": firestore.SERVER_TIMESTAMP,
            },
            merge=True,
        )
        batch.set(
            self._announcement_ref(video_id),
            {
                "video_id": video_id,
                "status": "manually_acknowledged",
                "channel_id": str(channel_id) if channel_id else None,
                "message_id": None,
                "acknowledged_by": str(actor_id) if actor_id else None,
                "acknowledged_at": firestore.SERVER_TIMESTAMP,
                "error": None,
                "updated_at": firestore.SERVER_TIMESTAMP,
            },
            merge=True,
        )
        batch.commit()

    async def migrate_from_file(self, path: str) -> str | None:
        existing = await self.load_last_video_id()
        if existing or not os.path.isfile(path):
            return existing

        try:
            with open(path, "r", encoding="utf-8") as file:
                video_id = file.read().strip()
        except OSError:
            return None

        if not video_id:
            return None

        await self.set_last_video_id(video_id)
        try:
            os.replace(path, f"{path}.migrated-{int(time.time())}")
        except OSError:
            pass
        return video_id

    async def claim_video(self, video_id: str, channel_id: int | None) -> bool:
        return await run_firestore(self._claim_video_sync, video_id, channel_id)

    def _claim_video_sync(self, video_id: str, channel_id: int | None) -> bool:
        db = get_firestore_client()
        firestore = get_firestore_module()
        ref = self._announcement_ref(video_id)
        transaction = db.transaction()

        @firestore.transactional
        def claim(transaction):
            snapshot = ref.get(transaction=transaction)
            if snapshot.exists:
                data = snapshot.to_dict() or {}
                status = data.get("status")
                if status in self.FINAL_STATUSES:
                    return False

            transaction.set(
                ref,
                {
                    "video_id": video_id,
                    "status": "pending",
                    "channel_id": str(channel_id) if channel_id else None,
                    "message_id": None,
                    "claimed_at": self._now(),
                    "sent_at": None,
                    "error": None,
                    "updated_at": firestore.SERVER_TIMESTAMP,
                },
                merge=True,
            )
            return True

        return bool(claim(transaction))

    async def mark_sent(self, video_id: str, channel_id: int | None, message_id: int | None) -> None:
        await run_firestore(self._mark_sent_sync, video_id, channel_id, message_id)

    def _mark_sent_sync(self, video_id: str, channel_id: int | None, message_id: int | None) -> None:
        db = get_firestore_client()
        firestore = get_firestore_module()
        batch = db.batch()
        batch.set(
            self._announcement_ref(video_id),
            {
                "video_id": video_id,
                "status": "sent",
                "channel_id": str(channel_id) if channel_id else None,
                "message_id": str(message_id) if message_id else None,
                "sent_at": firestore.SERVER_TIMESTAMP,
                "error": None,
                "updated_at": firestore.SERVER_TIMESTAMP,
            },
            merge=True,
        )
        batch.set(
            self._state_ref(),
            {
                "last_video_id": video_id,
                "updated_at": firestore.SERVER_TIMESTAMP,
            },
            merge=True,
        )
        batch.commit()

    async def mark_failed(self, video_id: str, error: str) -> None:
        await run_firestore(self._mark_failed_sync, video_id, error)

    def _mark_failed_sync(self, video_id: str, error: str) -> None:
        firestore = get_firestore_module()
        self._announcement_ref(video_id).set(
            {
                "video_id": video_id,
                "status": "send_failed",
                "error": error[:500],
                "failed_at": firestore.SERVER_TIMESTAMP,
                "updated_at": firestore.SERVER_TIMESTAMP,
            },
            merge=True,
        )
