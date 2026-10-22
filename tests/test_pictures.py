import tempfile
import unittest
import zipfile
from pathlib import Path

from farmsim_hub.core import mod_info
from farmsim_hub.pictures import MAX_ICON_BYTES, ModPictures, cache_key, decode_icon, icon_candidates, read_icon


class PictureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def zip_mod(self, reference, image_name, image=b"fixture artwork"):
        path = self.root / "FS25_Picture.zip"
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("modDesc.xml", f"<modDesc><iconFilename>{reference}</iconFilename></modDesc>")
            archive.writestr(image_name, image)
        return mod_info(path)

    def test_png_reference_finds_dds_in_zip_with_case_and_windows_slashes(self):
        mod = self.zip_mod("./Textures\\MyIcon.png", "textures/myicon.dds")
        self.assertEqual(mod["icon"], "./Textures\\MyIcon.png")
        self.assertEqual(read_icon(mod), b"fixture artwork")
        self.assertFalse(mod["error"])

    def test_exact_reference_is_preferred_over_fallback(self):
        mod = self.zip_mod("icon.png", "icon.png", b"preferred png")
        with zipfile.ZipFile(mod["path"], "a") as archive:
            archive.writestr("icon.dds", b"fallback dds")
        self.assertEqual(read_icon(mod), b"preferred png")

    def test_unpacked_mod_finds_artwork_and_refreshes_when_picture_changes(self):
        folder = self.root / "FS25_Folder"
        (folder / "images").mkdir(parents=True)
        (folder / "modDesc.xml").write_text("<modDesc><iconFilename>Images/icon.png</iconFilename></modDesc>")
        icon = folder / "images/Icon.DDS"
        icon.write_bytes(b"old artwork")
        mod = mod_info(folder)
        self.assertEqual(read_icon(mod), b"old artwork")
        before = cache_key(mod)
        icon.write_bytes(b"a newly updated picture")
        self.assertNotEqual(before, cache_key(mod))
        self.assertEqual(read_icon(mod), b"a newly updated picture")

    def test_external_and_parent_paths_are_not_followed(self):
        for reference in ["../secret.dds", "/secret.dds", "C:\\secret.dds", "$data/icon.dds"]:
            self.assertEqual(icon_candidates(reference), [])
            mod = self.zip_mod(reference, "../secret.dds")
            self.assertIsNone(read_icon(mod))

    def test_unpacked_picture_symlink_cannot_escape_mod_folder(self):
        folder = self.root / "FS25_Folder"
        folder.mkdir()
        (folder / "modDesc.xml").write_text("<modDesc><iconFilename>icon.dds</iconFilename></modDesc>")
        outside = self.root / "outside.dds"
        outside.write_bytes(b"outside")
        (folder / "icon.dds").symlink_to(outside)
        self.assertIsNone(read_icon(mod_info(folder)))

    def test_oversized_archive_picture_is_skipped(self):
        mod = self.zip_mod("icon.dds", "icon.dds", b"x" * (MAX_ICON_BYTES + 1))
        self.assertIsNone(read_icon(mod))

    def test_missing_picture_does_not_mark_mod_broken_or_stop_library(self):
        mod = self.zip_mod("missing.dds", "other.dds")
        self.assertFalse(mod["error"])
        pictures = ModPictures(self.root / "cache")
        pictures.prepare({mod["name"]: mod})
        self.assertFalse(pictures.images)
        self.assertIn(mod["name"], pictures.failures)

    def test_oversized_dds_dimensions_are_rejected_before_decoding(self):
        import struct
        data = bytearray(128)
        data[:4] = b"DDS "
        struct.pack_into("<II", data, 12, 100000, 100000)
        self.assertIsNone(decode_icon(bytes(data)))


if __name__ == "__main__":
    unittest.main()
