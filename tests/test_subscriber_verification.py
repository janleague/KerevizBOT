import unittest

from commands.subscriber_verification import (
    SubscriberRejectionModal,
    SubscriberVerificationModal,
    PROOF_UPLOAD_TIMEOUT_SECONDS,
    REQUEST_RETENTION_SECONDS,
    STAFF_ROLE_ID,
    SubscriberVerification,
    SUBMISSION_COOLDOWN_SECONDS,
    YOUTUBE_CHANNEL_URL,
    format_cooldown,
    is_proof_upload_category_name,
    is_supported_image_file,
    pending_proof_channel_expired,
    proof_upload_channel_name,
    seconds_until_next_submission,
    select_supported_image_attachment,
    should_delete_request,
)
from services.subscriber_verification_store import normalize_panel, normalize_request


class SubscriberVerificationConfigTests(unittest.TestCase):
    def test_uses_kereviz_youtube_channel_url(self):
        self.assertEqual(YOUTUBE_CHANNEL_URL, "https://www.youtube.com/@kerevizYT")

    def test_uses_proofs_category_for_upload_channels(self):
        self.assertTrue(is_proof_upload_category_name("PROOFS"))
        self.assertTrue(is_proof_upload_category_name(" proofs "))
        self.assertFalse(is_proof_upload_category_name("KEREVIZ BOT"))
        self.assertEqual(proof_upload_channel_name(123), "sub-proof-123")

    def test_validates_screenshot_image_files(self):
        self.assertTrue(is_supported_image_file("proof.txt", "image/png"))
        self.assertTrue(is_supported_image_file("proof.jpg", None))
        self.assertTrue(is_supported_image_file("proof.webp", "application/octet-stream"))
        self.assertFalse(is_supported_image_file("proof.pdf", "application/pdf"))
        self.assertFalse(is_supported_image_file("proof.txt", None))

    def test_selects_supported_proof_attachment(self):
        class Attachment:
            def __init__(self, filename, content_type):
                self.filename = filename
                self.content_type = content_type

        image = Attachment("proof.png", "image/png")
        self.assertIs(select_supported_image_attachment([Attachment("proof.pdf", "application/pdf"), image]), image)
        self.assertIsNone(select_supported_image_attachment([Attachment("proof.txt", "text/plain")]))

    def test_pending_proof_channel_expiry(self):
        session = {"expires_at": 1000 + PROOF_UPLOAD_TIMEOUT_SECONDS}
        self.assertFalse(pending_proof_channel_expired(session, current_ts=1000))
        self.assertTrue(pending_proof_channel_expired(session, current_ts=1000 + PROOF_UPLOAD_TIMEOUT_SECONDS))

    def test_member_can_submit_once_per_24_hours(self):
        records = {
            "old": {"guild_id": 1, "user_id": 10, "created_at": 1000},
            "recent": {"guild_id": 1, "user_id": 10, "created_at": 2000},
            "rejected": {"guild_id": 1, "user_id": 12, "created_at": 2500, "status": "rejected"},
            "other_user": {"guild_id": 1, "user_id": 11, "created_at": 2500},
            "other_guild": {"guild_id": 2, "user_id": 10, "created_at": 2500},
        }

        remaining = seconds_until_next_submission(records, 1, 10, current_ts=2600)
        self.assertEqual(remaining, SUBMISSION_COOLDOWN_SECONDS - 600)
        self.assertEqual(seconds_until_next_submission(records, 1, 11, current_ts=2600), SUBMISSION_COOLDOWN_SECONDS - 100)
        self.assertEqual(seconds_until_next_submission(records, 1, 12, current_ts=2600), 0)
        self.assertEqual(seconds_until_next_submission(records, 1, 10, current_ts=2000 + SUBMISSION_COOLDOWN_SECONDS), 0)

    def test_formats_cooldown_cleanly(self):
        self.assertEqual(format_cooldown(60), "1m")
        self.assertEqual(format_cooldown(3600), "1h")
        self.assertEqual(format_cooldown(3661), "1h 2m")

    def test_public_content_pings_on_final_decision(self):
        cog = SubscriberVerification(bot=None)

        self.assertEqual(
            cog._public_content({"status": "approved", "user_id": 123}),
            "<@123> your Subscriber verification request was approved.",
        )
        self.assertEqual(
            cog._public_content({"status": "rejected", "user_id": 123, "decision_reason": "Screenshot is unclear."}),
            "<@123> your Subscriber verification request was rejected.\nReason: Screenshot is unclear.",
        )
        self.assertIsNone(cog._public_content({"status": "pending", "user_id": 123}))

    def test_review_content_pings_staff_role(self):
        cog = SubscriberVerification(bot=None)
        self.assertEqual(
            cog._review_content({"status": "pending", "id": "abc"}),
            f"<@&{STAFF_ROLE_ID}> New Subscriber verification request.",
        )

    def test_public_embed_shows_deciding_staff_member(self):
        cog = SubscriberVerification(bot=None)
        embed = cog._public_embed(
            {
                "id": "abc",
                "status": "approved",
                "user_id": 123,
                "created_at": 1000,
                "decided_by_id": 456,
            }
        )

        fields = {field.name: field.value for field in embed.fields}
        self.assertEqual(fields["Approved By"], "<@456>")

    def test_deletes_only_old_final_requests(self):
        current_ts = 10000000
        old_ts = current_ts - REQUEST_RETENTION_SECONDS
        recent_ts = old_ts + 1

        self.assertTrue(should_delete_request({"status": "approved", "decided_at": old_ts}, current_ts=current_ts))
        self.assertTrue(should_delete_request({"status": "rejected", "created_at": old_ts}, current_ts=current_ts))
        self.assertFalse(should_delete_request({"status": "pending", "created_at": old_ts}, current_ts=current_ts))
        self.assertFalse(should_delete_request({"status": "approved", "decided_at": recent_ts}, current_ts=current_ts))

    def test_rejection_reason_is_optional(self):
        cog = SubscriberVerification(bot=None)
        modal = SubscriberRejectionModal(cog, review_message_id=123)
        self.assertFalse(modal.reason.required)

    def test_submission_modal_explains_temporary_channel_flow(self):
        cog = SubscriberVerification(bot=None)
        modal = SubscriberVerificationModal(cog)
        child_types = [type(child).__name__ for child in modal.children]
        text_content = "\n".join(getattr(child, "content", "") for child in modal.children)

        self.assertEqual(child_types, ["TextDisplay", "TextInput"])
        self.assertIn("private temporary channel", text_content)
        self.assertNotIn("Screenshot image URL", text_content)


class SubscriberVerificationStoreTests(unittest.TestCase):
    def test_normalizes_request_ids_and_status(self):
        record = normalize_request(
            {
                "id": 123,
                "guild_id": "1",
                "user_id": "2",
                "created_at": "3",
                "public_message_id": "4",
                "review_message_id": "5",
                "youtube_username": "  @kereviz  ",
                "screenshot_url": "  https://example.com/proof.png  ",
            }
        )

        self.assertEqual(record["id"], "123")
        self.assertEqual(record["guild_id"], 1)
        self.assertEqual(record["user_id"], 2)
        self.assertEqual(record["created_at"], 3)
        self.assertEqual(record["public_message_id"], 4)
        self.assertEqual(record["review_message_id"], 5)
        self.assertEqual(record["youtube_username"], "@kereviz")
        self.assertEqual(record["screenshot_url"], "https://example.com/proof.png")
        self.assertEqual(record["status"], "pending")

    def test_normalizes_panel_ids(self):
        panel = normalize_panel({"guild_id": "1", "channel_id": "2", "message_id": "3"})
        self.assertEqual(panel["guild_id"], 1)
        self.assertEqual(panel["channel_id"], 2)
        self.assertEqual(panel["message_id"], 3)
        self.assertEqual(panel["status"], "active")


if __name__ == "__main__":
    unittest.main()
