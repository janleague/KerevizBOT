import io
import unittest

from PIL import Image, ImageSequence

from services.gif_converter import GifConversionError, convert_image_bytes_to_gif


def _png_bytes(size=(96, 64), color=(40, 180, 80, 255)) -> bytes:
    image = Image.new("RGBA", size, color)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


class GifConverterTests(unittest.TestCase):
    def test_converts_static_image_to_gif(self):
        result = convert_image_bytes_to_gif(_png_bytes(), max_size=80)

        converted = Image.open(io.BytesIO(result.data))
        self.assertEqual(converted.format, "GIF")
        self.assertLessEqual(max(converted.size), 80)
        self.assertEqual(result.frame_count, 1)

    def test_preserves_animation_as_multiple_frames(self):
        frames = [
            Image.new("RGBA", (48, 48), (255, 0, 0, 255)),
            Image.new("RGBA", (48, 48), (0, 255, 0, 255)),
        ]
        buffer = io.BytesIO()
        frames[0].save(
            buffer,
            format="GIF",
            save_all=True,
            append_images=frames[1:],
            duration=[50, 70],
            loop=0,
        )

        result = convert_image_bytes_to_gif(buffer.getvalue(), max_size=48)
        converted = Image.open(io.BytesIO(result.data))

        self.assertEqual(converted.format, "GIF")
        self.assertEqual(sum(1 for _ in ImageSequence.Iterator(converted)), 2)
        self.assertEqual(result.frame_count, 2)

    def test_rejects_invalid_image_bytes(self):
        with self.assertRaises(GifConversionError):
            convert_image_bytes_to_gif(b"not an image")


if __name__ == "__main__":
    unittest.main()
