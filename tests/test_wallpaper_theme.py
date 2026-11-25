import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from wallpaper_theme import (
    MAX_IMAGE_BYTES,
    ThemeError,
    build_theme_manifest,
    build_theme_objects,
    check_theme_id,
    clean_image,
    theme_id_from_name,
)

PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000a49444154789c63000100000500010d0a2db40000000049454e44ae426082"
)


class ThemeTests(unittest.TestCase):
    def test_theme_id_comes_from_the_name(self):
        self.assertEqual(theme_id_from_name("Soft rain"), "soft_rain")

    def test_a_name_with_no_letters_is_rejected(self):
        with self.assertRaises(ThemeError):
            theme_id_from_name("!!!")

    def test_ids_already_in_the_app_are_rejected(self):
        with self.assertRaises(ThemeError):
            check_theme_id("soft_embrace")
        with self.assertRaises(ThemeError):
            check_theme_id("default")
        self.assertEqual(check_theme_id("Soft_Rain"), "soft_rain")

    def test_image_must_be_a_picture(self):
        with tempfile.TemporaryDirectory() as raw:
            folder = Path(raw)
            notes = folder / "notes.txt"
            notes.write_text("nope", encoding="utf-8")
            with self.assertRaises(ThemeError):
                clean_image(str(notes), label="wallpaper image", required=True)
            with self.assertRaises(ThemeError):
                clean_image("", label="wallpaper image", required=True)
            self.assertIsNone(clean_image("", label="preview image", required=False))

    def test_empty_and_oversized_images_are_rejected(self):
        with tempfile.TemporaryDirectory() as raw:
            folder = Path(raw)
            empty = folder / "empty.jpg"
            empty.write_bytes(b"")
            with self.assertRaises(ThemeError):
                clean_image(str(empty), label="wallpaper image", required=True)
            big = folder / "big.jpg"
            big.write_bytes(b"12345")
            with patch("wallpaper_theme.MAX_IMAGE_BYTES", 3):
                with self.assertRaises(ThemeError):
                    clean_image(str(big), label="wallpaper image", required=True)
            self.assertGreater(MAX_IMAGE_BYTES, 3)

    def test_upload_objects_are_the_wallpaper_and_manifest(self):
        with tempfile.TemporaryDirectory() as raw:
            folder = Path(raw)
            image = folder / "rain.JPG"
            image.write_bytes(PNG)
            manifest = build_theme_manifest(
                name="Soft rain",
                description="Grey-blue light",
                order=10,
                version=2,
                fallback_color="c5d0d8",
            )
            objects = build_theme_objects(image, None, theme_id="soft_rain", manifest=manifest)
            by_key = {item.key: item for item in objects}
            self.assertEqual(set(by_key), {"wallpapers/soft_rain/background.jpg", "wallpapers/soft_rain/manifest.json"})
            self.assertEqual(by_key["wallpapers/soft_rain/background.jpg"].content_type, "image/jpeg")
            stored = json.loads(by_key["wallpapers/soft_rain/manifest.json"].data)
            self.assertEqual(stored["name"], "Soft rain")
            self.assertEqual(stored["fallback_color"], "#C5D0D8")
            self.assertEqual(stored["order"], 10)

    def test_preview_is_uploaded_when_given(self):
        with tempfile.TemporaryDirectory() as raw:
            folder = Path(raw)
            image = folder / "rain.png"
            preview = folder / "thumb.webp"
            image.write_bytes(PNG)
            preview.write_bytes(b"webp")
            manifest = build_theme_manifest(
                name="Soft rain",
                description="",
                order=100,
                version=1,
                fallback_color="",
            )
            objects = build_theme_objects(image, preview, theme_id="soft_rain", manifest=manifest)
            keys = {item.key for item in objects}
            self.assertIn("wallpapers/soft_rain/preview.webp", keys)
            self.assertNotIn("description", json.loads(next(item.data for item in objects if item.key.endswith(".json"))))

    def test_bad_color_and_long_description_are_rejected(self):
        with self.assertRaises(ThemeError):
            build_theme_manifest(name="Rain", description="", order=1, version=1, fallback_color="blue")
        with self.assertRaises(ThemeError):
            build_theme_manifest(name="Rain", description="x" * 241, order=1, version=1, fallback_color="#D7F0DE")


if __name__ == "__main__":
    unittest.main()
