import shutil
import unittest
from unittest.mock import patch

from farmsim_hub.core import HubError
from farmsim_hub.modhub import ModHubClient, SearchPage
from tests.test_core import make_mod
from tests.test_workflows import TemporaryHub
from tests.test_modhub import detail_html


class BrowseTests(TemporaryHub, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.client = ModHubClient(self.hub.data)

    def test_search_reads_pictures_authors_and_pagination(self):
        page = '''<div class="mod-item"><a href="mod.php?mod_id=123456&amp;title=fs2025"><img src="https://cdn40.giants-software.com/icon.jpg"></a><div><h4>Example tractor</h4><p><span>By: Fixture author</span></p></div></div><a href="mods.php?title=fs2025&amp;page=1">2</a>'''
        with patch.object(self.client, "_fetch", return_value=page), patch.object(self.client, "_open", side_effect=OSError("Picture unavailable")):
            result = self.client.search("Example", 0)
        self.assertTrue(result["has_next"])
        item = result["items"][0]
        self.assertEqual(item["title"], "Example tractor")
        self.assertEqual(item["author"], "Fixture author")
        self.assertEqual(item["id"], "123456")
        self.assertEqual(item["picture"], "")

    def test_new_download_is_validated_and_imported_without_touching_loadouts_or_game(self):
        staged = self.home / "new.zip"
        make_mod(staged, title="Browse mod", version="2.0.0.0")
        before = self.career.read_bytes(), self.settings.read_bytes(), self.loadout["mods"][:]
        with patch.object(self.client, "_fetch", return_value=detail_html(name="FS25_Browse.zip")), patch.object(self.client, "_download", side_effect=lambda url, dest, progress=None: shutil.copy2(staged, dest)):
            result = self.client.download_mod(self.hub, "123456")
        self.assertEqual(result["path"].read_bytes(), staged.read_bytes())
        self.assertEqual((self.career.read_bytes(), self.settings.read_bytes(), self.loadout["mods"]), before)
        self.hub.scan()
        self.assertIn("FS25_Browse.zip", self.hub.mods)

    def test_browse_download_never_overwrites_an_installed_mod(self):
        before = (self.library / "FS25_Existing.zip").read_bytes()
        with patch.object(self.client, "_fetch", return_value=detail_html()), patch.object(self.client, "_download") as download:
            with self.assertRaisesRegex(HubError, "already installed"):
                self.client.download_mod(self.hub, "123456")
        download.assert_not_called()
        self.assertEqual((self.library / "FS25_Existing.zip").read_bytes(), before)

    def test_invalid_download_never_enters_library(self):
        staged = self.home / "broken.zip"
        staged.write_bytes(b"Not a ZIP")
        with patch.object(self.client, "_fetch", return_value=detail_html(name="FS25_Browse.zip")), patch.object(self.client, "_download", side_effect=lambda url, dest, progress=None: shutil.copy2(staged, dest)):
            with self.assertRaises(HubError):
                self.client.download_mod(self.hub, "123456")
        self.assertFalse((self.hub.data / "imports/FS25_Browse.zip").exists())
        self.assertFalse(list(self.hub.data.glob(".hub-download-*")))


if __name__ == "__main__":
    unittest.main()
