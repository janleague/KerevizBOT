from pathlib import Path
from typing import Any

from services.firebase_client import get_firestore_client, get_firestore_module, run_firestore


COLLECTION_NAME = "minecraft_servers"


def normalize_host(host: str) -> str:
    return host.strip().lower()


def document_id_for_host(host: str) -> str:
    return normalize_host(host).replace("/", "__slash__")


def load_seed_hosts(path: str | Path) -> list[str]:
    hosts: list[str] = []
    seen: set[str] = set()
    seed_path = Path(path)

    if not seed_path.is_file():
        return hosts

    for raw_line in seed_path.read_text(encoding="utf-8").splitlines():
        host = raw_line.strip()
        if not host or host.startswith("#"):
            continue
        key = normalize_host(host)
        if key in seen:
            continue
        seen.add(key)
        hosts.append(host)

    return hosts


class MinecraftServerStore:
    @property
    def storage_label(self) -> str:
        return "Firebase Firestore"

    def _collection(self):
        return get_firestore_client().collection(COLLECTION_NAME)

    async def list_servers(self) -> list[str]:
        return await run_firestore(self._list_servers_sync)

    def _list_servers_sync(self) -> list[str]:
        rows: list[tuple[int, str, str]] = []

        for snapshot in self._collection().stream():
            data = snapshot.to_dict() or {}
            host = str(data.get("host") or snapshot.id).strip()
            if not host:
                continue
            order = self._safe_order(data.get("order"))
            rows.append((order, normalize_host(host), host))

        rows.sort(key=lambda row: (row[0], row[1]))
        hosts: list[str] = []
        seen: set[str] = set()
        for _, normalized_host, host in rows:
            if normalized_host in seen:
                continue
            seen.add(normalized_host)
            hosts.append(host)
        return hosts

    async def add_server(self, host: str) -> bool:
        return await run_firestore(self._add_server_sync, host)

    def _add_server_sync(self, host: str) -> bool:
        clean_host = host.strip()
        document_id = document_id_for_host(clean_host)
        if not document_id:
            raise ValueError("Host cannot be empty.")

        collection = self._collection()
        ref = collection.document(document_id)
        if ref.get().exists:
            return False

        firestore = get_firestore_module()
        next_order = self._next_order(collection)
        ref.set(
            {
                "host": clean_host,
                "normalized_host": normalize_host(clean_host),
                "order": next_order,
                "source": "command",
                "created_at": firestore.SERVER_TIMESTAMP,
                "updated_at": firestore.SERVER_TIMESTAMP,
            }
        )
        return True

    async def seed_from_file(self, path: str | Path) -> int:
        hosts = load_seed_hosts(path)
        if not hosts:
            return 0
        return await run_firestore(self._seed_hosts_sync, hosts)

    def _seed_hosts_sync(self, hosts: list[str]) -> int:
        db = get_firestore_client()
        firestore = get_firestore_module()
        collection = self._collection()
        existing_ids = {snapshot.id for snapshot in collection.stream()}

        batch = db.batch()
        writes = 0
        added = 0

        def commit_if_needed(force: bool = False) -> None:
            nonlocal batch, writes
            if writes and (force or writes >= 450):
                batch.commit()
                batch = db.batch()
                writes = 0

        for order, host in enumerate(hosts):
            document_id = document_id_for_host(host)
            if not document_id or document_id in existing_ids:
                continue
            batch.set(
                collection.document(document_id),
                self._document_payload(host, order, "seed", firestore),
            )
            existing_ids.add(document_id)
            writes += 1
            added += 1
            commit_if_needed()

        commit_if_needed(force=True)
        return added

    async def replace_from_file(self, path: str | Path) -> int:
        hosts = load_seed_hosts(path)
        if not hosts:
            return 0
        return await run_firestore(self._replace_hosts_sync, hosts)

    def _replace_hosts_sync(self, hosts: list[str]) -> int:
        db = get_firestore_client()
        firestore = get_firestore_module()
        collection = self._collection()
        wanted_ids = {document_id_for_host(host) for host in hosts if document_id_for_host(host)}

        batch = db.batch()
        writes = 0

        def commit_if_needed(force: bool = False) -> None:
            nonlocal batch, writes
            if writes and (force or writes >= 450):
                batch.commit()
                batch = db.batch()
                writes = 0

        for snapshot in collection.stream():
            if snapshot.id not in wanted_ids:
                batch.delete(snapshot.reference)
                writes += 1
                commit_if_needed()

        for order, host in enumerate(hosts):
            document_id = document_id_for_host(host)
            if not document_id:
                continue
            batch.set(
                collection.document(document_id),
                self._document_payload(host, order, "servers.txt", firestore),
                merge=True,
            )
            writes += 1
            commit_if_needed()

        commit_if_needed(force=True)
        return len(hosts)

    @staticmethod
    def _document_payload(host: str, order: int, source: str, firestore: Any) -> dict[str, Any]:
        return {
            "host": host.strip(),
            "normalized_host": normalize_host(host),
            "order": int(order),
            "source": source,
            "updated_at": firestore.SERVER_TIMESTAMP,
        }

    @staticmethod
    def _safe_order(value: Any) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return 1_000_000

    @classmethod
    def _next_order(cls, collection) -> int:
        max_order = -1
        for snapshot in collection.stream():
            order = cls._safe_order((snapshot.to_dict() or {}).get("order"))
            if order > max_order and order < 1_000_000:
                max_order = order
        return max_order + 1
