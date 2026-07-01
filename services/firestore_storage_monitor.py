import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from services.firebase_client import (
    get_firebase_credential_path,
    get_firestore_client,
    get_firestore_module,
    run_firestore,
)


METRIC_TYPE = "firestore.googleapis.com/storage/data_and_index_storage_bytes"
MONITORING_SCOPE = "https://www.googleapis.com/auth/monitoring.read"
STATE_DOCUMENT_ID = "firestore_storage_alert"
DEFAULT_LIMIT_BYTES = 1024 * 1024 * 1024
DEFAULT_THRESHOLDS = (70, 85, 95)
DEFAULT_RESET_PERCENT = 65.0
PERMISSION_ALERT_COOLDOWN_SECONDS = 24 * 60 * 60


class FirestoreMonitoringError(RuntimeError):
    pass


class FirestoreMonitoringPermissionError(FirestoreMonitoringError):
    pass


@dataclass(frozen=True)
class FirestoreStorageUsage:
    used_bytes: int
    limit_bytes: int
    percent: float
    measured_at: datetime | None
    database_count: int
    metric_type: str = METRIC_TYPE


def parse_thresholds(raw_value: str | None, default: tuple[int, ...] = DEFAULT_THRESHOLDS) -> tuple[int, ...]:
    if not raw_value:
        return default

    parsed: list[int] = []
    for part in raw_value.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            value = int(part)
        except ValueError:
            continue
        if 0 < value <= 100 and value not in parsed:
            parsed.append(value)

    return tuple(sorted(parsed)) if parsed else default


def calculate_percent(used_bytes: int, limit_bytes: int) -> float:
    if limit_bytes <= 0:
        return 0.0
    return max(0.0, (max(0, used_bytes) / limit_bytes) * 100)


def classify_threshold(percent: float, thresholds: tuple[int, ...] = DEFAULT_THRESHOLDS) -> int | None:
    reached = [threshold for threshold in thresholds if percent >= threshold]
    return max(reached) if reached else None


def should_reset_alert(percent: float, reset_percent: float = DEFAULT_RESET_PERCENT) -> bool:
    return percent < reset_percent


def should_send_threshold_alert(current_level: int | None, last_alerted_level: int | None) -> bool:
    if current_level is None:
        return False
    if last_alerted_level is None:
        return True
    return current_level > last_alerted_level


def should_send_permission_alert(last_alert_at: int | None, current_ts: int | None = None) -> bool:
    if last_alert_at is None:
        return True
    now = int(current_ts if current_ts is not None else time.time())
    return now - int(last_alert_at) >= PERMISSION_ALERT_COOLDOWN_SECONDS


def format_bytes(value: int) -> str:
    amount = float(max(0, value))
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    for unit in units:
        if amount < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{int(amount)} {unit}"
            return f"{amount:.2f} {unit}"
        amount /= 1024
    return f"{amount:.2f} TiB"


def normalize_alert_state(record: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(record, dict):
        return {
            "last_alerted_level": None,
            "last_permission_alert_at": None,
        }

    normalized = dict(record)
    for key in ("last_alerted_level", "last_permission_alert_at", "last_used_bytes"):
        value = normalized.get(key)
        try:
            normalized[key] = int(value) if value is not None else None
        except (TypeError, ValueError):
            normalized[key] = None

    value = normalized.get("last_percent")
    try:
        normalized["last_percent"] = float(value) if value is not None else None
    except (TypeError, ValueError):
        normalized["last_percent"] = None

    return normalized


def _parse_point_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _point_numeric_value(point: dict[str, Any]) -> int | None:
    value = point.get("value") if isinstance(point, dict) else None
    if not isinstance(value, dict):
        return None
    raw_value = value.get("int64Value", value.get("doubleValue"))
    if raw_value is None:
        return None
    try:
        return int(float(raw_value))
    except (TypeError, ValueError):
        return None


def parse_monitoring_timeseries(payload: dict[str, Any]) -> tuple[int, datetime | None, int]:
    total_bytes = 0
    latest_time: datetime | None = None
    database_count = 0

    for series in payload.get("timeSeries") or []:
        if not isinstance(series, dict):
            continue
        points = series.get("points") or []
        if not points:
            continue
        latest_point = points[0]
        point_value = _point_numeric_value(latest_point)
        if point_value is None:
            continue

        total_bytes += max(0, point_value)
        database_count += 1

        point_time = _parse_point_time((latest_point.get("interval") or {}).get("endTime"))
        if point_time and (latest_time is None or point_time > latest_time):
            latest_time = point_time

    return total_bytes, latest_time, database_count


class FirestoreStorageAlertStore:
    def _state_ref(self):
        return get_firestore_client().collection("bot_state").document(STATE_DOCUMENT_ID)

    async def load_state(self) -> dict[str, Any]:
        return await run_firestore(self._load_state_sync)

    def _load_state_sync(self) -> dict[str, Any]:
        snapshot = self._state_ref().get()
        if not snapshot.exists:
            return normalize_alert_state(None)
        return normalize_alert_state(snapshot.to_dict() or {})

    async def record_threshold_alert(self, level: int, usage: FirestoreStorageUsage) -> None:
        await run_firestore(self._record_threshold_alert_sync, int(level), usage)

    def _record_threshold_alert_sync(self, level: int, usage: FirestoreStorageUsage) -> None:
        firestore = get_firestore_module()
        self._state_ref().set(
            {
                "last_alerted_level": int(level),
                "last_used_bytes": int(usage.used_bytes),
                "last_percent": float(usage.percent),
                "last_threshold_alert_at": firestore.SERVER_TIMESTAMP,
                "updated_at": firestore.SERVER_TIMESTAMP,
            },
            merge=True,
        )

    async def reset_threshold_alert(self, usage: FirestoreStorageUsage) -> None:
        await run_firestore(self._reset_threshold_alert_sync, usage)

    def _reset_threshold_alert_sync(self, usage: FirestoreStorageUsage) -> None:
        firestore = get_firestore_module()
        self._state_ref().set(
            {
                "last_alerted_level": None,
                "last_used_bytes": int(usage.used_bytes),
                "last_percent": float(usage.percent),
                "last_reset_at": firestore.SERVER_TIMESTAMP,
                "updated_at": firestore.SERVER_TIMESTAMP,
            },
            merge=True,
        )

    async def record_permission_alert(self, current_ts: int, error: str) -> None:
        await run_firestore(self._record_permission_alert_sync, int(current_ts), str(error))

    def _record_permission_alert_sync(self, current_ts: int, error: str) -> None:
        firestore = get_firestore_module()
        self._state_ref().set(
            {
                "last_permission_alert_at": int(current_ts),
                "last_permission_error": error[:500],
                "updated_at": firestore.SERVER_TIMESTAMP,
            },
            merge=True,
        )


class FirestoreStorageMonitorClient:
    def __init__(
        self,
        project_id: str,
        limit_bytes: int = DEFAULT_LIMIT_BYTES,
        metric_type: str = METRIC_TYPE,
    ):
        self.project_id = project_id
        self.limit_bytes = int(limit_bytes)
        self.metric_type = metric_type

    def _credential_token(self) -> str:
        cred_path = get_firebase_credential_path()
        if not cred_path:
            raise FirestoreMonitoringError("FIREBASE_CREDENTIALS_PATH or GOOGLE_APPLICATION_CREDENTIALS is required.")

        try:
            from google.auth.transport.requests import Request
            from google.oauth2 import service_account
        except ImportError as exc:
            raise FirestoreMonitoringError("google-auth is required to read Cloud Monitoring metrics.") from exc

        credentials = service_account.Credentials.from_service_account_file(
            cred_path,
            scopes=[MONITORING_SCOPE],
        )
        credentials.refresh(Request())
        return str(credentials.token)

    async def fetch_usage(self) -> FirestoreStorageUsage:
        return await run_firestore(self._fetch_usage_sync)

    def _fetch_usage_sync(self) -> FirestoreStorageUsage:
        if not self.project_id:
            raise FirestoreMonitoringError("FIREBASE_PROJECT_ID is required to read Cloud Monitoring metrics.")

        end_time = datetime.now(timezone.utc)
        start_time = end_time - timedelta(hours=24)
        params = {
            "filter": f'metric.type = "{self.metric_type}"',
            "interval.startTime": start_time.isoformat().replace("+00:00", "Z"),
            "interval.endTime": end_time.isoformat().replace("+00:00", "Z"),
            "pageSize": "100",
        }

        payload = self._monitoring_get(params)
        used_bytes, measured_at, database_count = parse_monitoring_timeseries(payload)
        return FirestoreStorageUsage(
            used_bytes=used_bytes,
            limit_bytes=self.limit_bytes,
            percent=calculate_percent(used_bytes, self.limit_bytes),
            measured_at=measured_at,
            database_count=database_count,
            metric_type=self.metric_type,
        )

    def _monitoring_get(self, params: dict[str, str]) -> dict[str, Any]:
        base_url = f"https://monitoring.googleapis.com/v3/projects/{self.project_id}/timeSeries"
        token = self._credential_token()
        payload: dict[str, Any] = {"timeSeries": []}
        page_token = None

        while True:
            request_params = dict(params)
            if page_token:
                request_params["pageToken"] = page_token
            url = f"{base_url}?{urllib.parse.urlencode(request_params)}"
            request = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})

            try:
                with urllib.request.urlopen(request, timeout=20) as response:
                    page = json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                body = exc.read().decode("utf-8", "replace") if exc.fp else ""
                if exc.code == 403:
                    raise FirestoreMonitoringPermissionError(
                        "Cloud Monitoring denied access. Grant roles/monitoring.viewer to the Firebase service account."
                    ) from exc
                raise FirestoreMonitoringError(f"Cloud Monitoring returned HTTP {exc.code}: {body[:300]}") from exc
            except urllib.error.URLError as exc:
                raise FirestoreMonitoringError(f"Cloud Monitoring request failed: {exc.reason}") from exc

            payload["timeSeries"].extend(page.get("timeSeries") or [])
            page_token = page.get("nextPageToken")
            if not page_token:
                return payload
