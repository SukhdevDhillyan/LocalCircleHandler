import json
import tempfile
import unittest
import zipfile
from io import BytesIO
from pathlib import Path

from sticker_pack import (
    PackError,
    audience_lock,
    build_manifest,
    build_objects,
    check_audience,
    cover_choice,
    humanize_slug,
    inspect_folder,
    pack_id_from_name,
    stale_keys,
)

PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000a49444154789c63000100000500010d0a2db40000000049454e44ae426082"
)


def _write(folder: Path, name: str, data: bytes = PNG) -> None:
    (folder / name).write_bytes(data)


class PackTests(unittest.TestCase):
    def test_pack_id_comes_from_the_name(self):
        self.assertEqual(pack_id_from_name("Monsoon Moods"), "monsoon_moods")
        self.assertEqual(pack_id_from_name("Office!"), "office")

    def test_a_name_with_no_letters_is_rejected(self):
        with self.assertRaises(PackError):
            pack_id_from_name("!!!")

    def test_reserved_ids_keep_the_audience_the_app_hard_codes(self):
        self.assertEqual(audience_lock("everyday_desi_extra"), "both")
        self.assertEqual(audience_lock("male_office"), "male")
        self.assertEqual(audience_lock("female_office"), "female")
        self.assertIsNone(audience_lock("monsoon_moods"))
        with self.assertRaises(PackError):
            check_audience("male_office", "female")
        self.assertEqual(check_audience("monsoon_moods", "female"), "female")

    def test_labels_match_the_chat_service(self):
        self.assertEqual(humanize_slug("01_kya_scene_hai"), "Kya scene hai")

    def test_folder_scan_keeps_images_cover_and_manifest_labels(self):
        with tempfile.TemporaryDirectory() as raw:
            folder = Path(raw)
            _write(folder, "02_chai.webp", b"webp")
            _write(folder, "01_hi.png")
            _write(folder, "cover.webp", b"cover")
            _write(folder, "notes.txt", b"nope")
            (folder / "extra").mkdir()
            (folder / "manifest.json").write_text(
                json.dumps({"name": "Chai", "labels": {"02_chai": "Chai?"}}),
                encoding="utf-8",
            )
            scan = inspect_folder(folder)
            self.assertEqual([item.file for item in scan.stickers], ["01_hi.png", "02_chai.webp"])
            self.assertEqual(scan.stickers[1].label, "Chai?")
            self.assertEqual(scan.stickers[0].label, "Hi")
            self.assertEqual(scan.cover_file, "cover.webp")
            self.assertEqual(scan.ignored, ["extra/ (a subfolder is not uploaded)", "notes.txt"])

    def test_empty_folder_is_rejected(self):
        with tempfile.TemporaryDirectory() as raw:
            with self.assertRaises(PackError):
                inspect_folder(Path(raw))

    def test_upload_objects_include_manifest_zip_and_a_chosen_cover(self):
        with tempfile.TemporaryDirectory() as raw:
            folder = Path(raw)
            _write(folder, "chai.webp", b"webp-bytes")
            scan = inspect_folder(folder)
            manifest = build_manifest(
                name="Chai",
                description="Hot drinks",
                order=20,
                version=1,
                audience="both",
                labels={},
            )
            objects = build_objects(scan, pack_id="chai", manifest=manifest, cover=cover_choice(scan, "chai.webp"))
            keys = {item.key for item in objects}
            self.assertEqual(
                keys,
                {
                    "packs/chai/chai.webp",
                    "packs/chai/manifest.json",
                    "packs/chai/pack.zip",
                },
            )
            zipped = next(item.data for item in objects if item.key.endswith("pack.zip"))
            with zipfile.ZipFile(BytesIO(zipped)) as archive:
                self.assertEqual(sorted(archive.namelist()), ["chai.webp"])
            stored = json.loads(next(item.data for item in objects if item.key.endswith("manifest.json")))
            self.assertEqual(stored["audience"], "both")
            self.assertEqual(stored["name"], "Chai")

    def test_stale_keys_are_files_the_new_pack_does_not_keep(self):
        keep = {"packs/chai/a.webp", "packs/chai/manifest.json"}
        self.assertEqual(
            stale_keys(["packs/chai/a.webp", "packs/chai/old.webp", "packs/chai/manifest.json"], keep),
            ["packs/chai/old.webp"],
        )


if __name__ == "__main__":
    unittest.main()
