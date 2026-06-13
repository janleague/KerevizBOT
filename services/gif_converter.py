import io
from dataclasses import dataclass

from PIL import Image, ImageOps, ImageSequence, UnidentifiedImageError


DEFAULT_MAX_SIZE = 512
MAX_FRAMES = 60
MAX_OUTPUT_BYTES = 8 * 1024 * 1024
MAX_PIXELS = 16_000_000
MIN_SIZE = 64
DEFAULT_FRAME_DURATION_MS = 100

try:
    RESAMPLE = Image.Resampling.LANCZOS
except AttributeError:  # pragma: no cover - compatibility for older Pillow
    RESAMPLE = Image.LANCZOS


class GifConversionError(ValueError):
    pass


@dataclass(frozen=True)
class GifConversionResult:
    data: bytes
    width: int
    height: int
    frame_count: int


def _target_size(width: int, height: int, max_size: int) -> tuple[int, int]:
    scale = min(1.0, max_size / max(width, height))
    return max(1, round(width * scale)), max(1, round(height * scale))


def _frame_duration_ms(frame: Image.Image, fallback: int = DEFAULT_FRAME_DURATION_MS) -> int:
    duration = frame.info.get("duration", fallback)
    try:
        duration = int(duration)
    except (TypeError, ValueError):
        duration = fallback
    return max(20, duration)


def _copy_frame(frame: Image.Image, size: tuple[int, int]) -> Image.Image:
    image = frame.convert("RGBA")
    fitted = ImageOps.contain(image, size, RESAMPLE)
    canvas = Image.new("RGBA", size, (0, 0, 0, 0))
    offset = ((size[0] - fitted.width) // 2, (size[1] - fitted.height) // 2)
    canvas.paste(fitted, offset, fitted)
    return canvas


def _collect_frames(
    image: Image.Image,
    size: tuple[int, int],
    frame_stride: int,
) -> tuple[list[Image.Image], list[int]]:
    frames: list[Image.Image] = []
    durations: list[int] = []
    pending_duration = 0

    for index, frame in enumerate(ImageSequence.Iterator(image)):
        pending_duration += _frame_duration_ms(frame)

        should_keep = index % frame_stride == 0
        if should_keep:
            frames.append(_copy_frame(frame, size))
            durations.append(pending_duration)
            pending_duration = 0
            if len(frames) >= MAX_FRAMES:
                break

    if pending_duration and durations:
        durations[-1] += pending_duration

    if not frames:
        frames.append(_copy_frame(image, size))
        durations.append(DEFAULT_FRAME_DURATION_MS)

    return frames, durations


def _save_gif(frames: list[Image.Image], durations: list[int]) -> bytes:
    buffer = io.BytesIO()
    duration = durations if len(frames) > 1 else durations[0]
    frames[0].save(
        buffer,
        format="GIF",
        save_all=len(frames) > 1,
        append_images=frames[1:],
        duration=duration,
        loop=0,
        disposal=2,
        optimize=True,
    )
    return buffer.getvalue()


def convert_image_bytes_to_gif(
    image_bytes: bytes,
    *,
    max_size: int = DEFAULT_MAX_SIZE,
    max_output_bytes: int = MAX_OUTPUT_BYTES,
) -> GifConversionResult:
    if not image_bytes:
        raise GifConversionError("The uploaded file is empty.")

    try:
        image = Image.open(io.BytesIO(image_bytes))
    except UnidentifiedImageError as exc:
        raise GifConversionError("I could not read that file as an image.") from exc

    width, height = image.size
    if width <= 0 or height <= 0:
        raise GifConversionError("The image has invalid dimensions.")
    if width * height > MAX_PIXELS:
        raise GifConversionError("That image is too large to process safely.")

    requested_size = max(MIN_SIZE, min(int(max_size), 1024))
    candidate_sizes = []
    current_size = requested_size
    while current_size >= MIN_SIZE:
        if current_size not in candidate_sizes:
            candidate_sizes.append(current_size)
        current_size = int(current_size * 0.75)

    last_result: GifConversionResult | None = None
    for candidate_size in candidate_sizes:
        size = _target_size(width, height, candidate_size)
        for frame_stride in (1, 2, 3, 4):
            try:
                image.seek(0)
            except EOFError:
                pass
            frames, durations = _collect_frames(image, size, frame_stride)
            data = _save_gif(frames, durations)
            result = GifConversionResult(data=data, width=size[0], height=size[1], frame_count=len(frames))
            if len(data) <= max_output_bytes:
                return result
            last_result = result

    if last_result:
        size_mb = len(last_result.data) / (1024 * 1024)
        raise GifConversionError(f"The GIF is still too large after compression ({size_mb:.1f} MB).")
    raise GifConversionError("The image could not be converted to GIF.")
