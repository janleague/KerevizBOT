import re
from urllib.parse import parse_qs, urlparse


YOUTUBE_VIDEO_ID_RE = re.compile(r"^[0-9A-Za-z_-]{11}$")


def normalize_youtube_video_id(value: str | None) -> str | None:
    """Return a canonical YouTube video ID from a raw ID or common video URL."""
    if not value:
        return None

    raw_value = value.strip().strip("<>")
    if YOUTUBE_VIDEO_ID_RE.fullmatch(raw_value):
        return raw_value

    parsed = urlparse(raw_value)
    if not parsed.scheme and not parsed.netloc:
        return None

    query_video_id = parse_qs(parsed.query).get("v", [None])[0]
    if query_video_id and YOUTUBE_VIDEO_ID_RE.fullmatch(query_video_id):
        return query_video_id

    path_parts = [part for part in parsed.path.split("/") if part]
    host = parsed.netloc.lower()

    if host.endswith("youtu.be") and path_parts:
        candidate = path_parts[0]
    elif path_parts and path_parts[0] in {"shorts", "live", "embed", "v"} and len(path_parts) > 1:
        candidate = path_parts[1]
    else:
        candidate = None

    if candidate and YOUTUBE_VIDEO_ID_RE.fullmatch(candidate):
        return candidate
    return None
