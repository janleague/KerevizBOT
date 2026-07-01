import asyncio
import os
from pathlib import Path
from typing import Any, Callable, TypeVar


T = TypeVar("T")

_db = None
_firestore_module = None


class FirebaseConfigError(RuntimeError):
    pass


def _credential_path() -> str | None:
    raw_path = os.getenv("FIREBASE_CREDENTIALS_PATH") or os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    if not raw_path:
        return None

    expanded = os.path.expandvars(os.path.expanduser(raw_path))
    path = Path(expanded)
    if not path.is_absolute():
        path = Path.cwd() / path
    return str(path.resolve())


def get_firebase_credential_path() -> str | None:
    return _credential_path()


def get_firestore_client():
    global _db, _firestore_module

    if _db is not None:
        return _db

    try:
        import firebase_admin
        from firebase_admin import credentials, firestore
    except ImportError as exc:
        raise FirebaseConfigError("firebase-admin is not installed. Run `pip install -r requirements.txt`.") from exc

    options: dict[str, Any] = {}
    project_id = os.getenv("FIREBASE_PROJECT_ID")
    if project_id:
        options["projectId"] = project_id

    try:
        firebase_admin.get_app()
    except ValueError:
        cred_path = _credential_path()
        if cred_path:
            if not os.path.isfile(cred_path):
                raise FirebaseConfigError(f"Firebase credential file was not found: {cred_path}")
            cred = credentials.Certificate(cred_path)
            firebase_admin.initialize_app(cred, options or None)
        else:
            firebase_admin.initialize_app(options=options or None)

    _firestore_module = firestore
    _db = firestore.client()
    return _db


def get_firestore_module():
    if _firestore_module is None:
        get_firestore_client()
    return _firestore_module


async def run_firestore(func: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    return await asyncio.to_thread(func, *args, **kwargs)
