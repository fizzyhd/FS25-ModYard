import copy
import io
import json
import shutil
import unittest
from pathlib import Path
from unittest.mock import patch

from farmsim_hub import core
from farmsim_hub.core import HubError
from farmsim_hub.modhub import ModHubClient, official_url, parse_mod_page, version_number
from tests.test_core import make_mod
from tests.test_workflows import TemporaryHub


def detail_html(name="FS25_Existing.zip", version="2.0.0.0", game="Farming Simulator 25"):
    cells = ["Game", game, "Author", "Fixture author", "Filename", name, "Version", version]
    return '<h2>Existing</h2>' + ''.join('<div class="table-cell">' + c + '</div>' for c in cells) + (
        '<a href="https://cdn26.giants-software.com/modHub/storage/00123456/' + name + '">DOWNLOAD</a>')


class ModHubTests(TemporaryHub, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.client = ModHubClient(self.hub.data)
        self.entry = parse_mod_page(detail_html(), "123456")
        self.client.cache["entries"]["FS25_Existing.zip"] = self.entry
        self.source = self.library / "FS25_Existing.zip"
        self.updated = self.home / "new-version.zip"
        make_mod(self.updated, title="Existing", version="2.0.0.0", deps=["FS25_New"])
        self.old_bytes = self.source.read_bytes()
        self.detail_patch = patch.object(self.client, "detail", return_value=self.entry)
        self.download_patch = patch.object(self.client, "_download", side_effect=lambda url, dest, progress=None: shutil.copy2(self.updated, dest))
        self.detail_patch.start()
        self.download_patch.start()

    def tearDown(self):
        self.download_patch.stop()
        self.detail_patch.stop()
        super().tearDown()

    def test_official_page_metadata_and_numeric_version_comparison(self):
        self.assertEqual(self.entry["filename"], "FS25_Existing.zip")
        self.assertGreater(version_number("1.10.0.0"), version_number("1.9.0.0"))
        self.assertEqual(version_number("1.0"), version_number("1.0.0.0"))
        self.assertIsNone(version_number("beta"))
        with self.assertRaises(HubError):
            parse_mod_page(detail_html(game="Farming Simulator 22"), "123456")

    def test_only_official_https_download_hosts_are_accepted(self):
        self.assertTrue(official_url("https://cdn26.giants-software.com/modHub/mod.zip"))
        for url in ["http://cdn26.giants-software.com/mod.zip", "https://giants-software.com.evil.example/mod.zip", "https://example.com/mod.zip", "https://name:password@cdn26.giants-software.com/mod.zip"]:
            self.assertFalse(official_url(url))

    def test_older_official_version_and_failed_checks_never_offer_replacement(self):
        self.client.cache["entries"]["FS25_Existing.zip"]["version"] = "0.9.0.0"
        result = self.client.results(self.hub.mods)[0]
        self.assertEqual(result["status"], "Local version is newer")
        self.assertFalse(result["can_update"])
        self.client.cache["entries"]["FS25_Existing.zip"].update(version="2.0.0.0", error="Offline")
        self.assertFalse(self.client.results(self.hub.mods)[0]["can_update"])

    def test_link_requires_the_exact_filename(self):
        mod = self.hub.mods["FS25_New.zip"]
        with self.assertRaisesRegex(HubError, "not FS25_New.zip"):
            self.client.link(mod, "https://www.farming-simulator.com/mod.php?mod_id=123456")
        self.assertNotIn("FS25_New.zip", self.client.cache["entries"])

    def test_search_title_is_not_enough_to_match_a_different_file(self):
        page = '<div class="mod-item"><a href="mod.php?mod_id=123456&amp;title=fs2025">Info</a><h4>New mod</h4></div>'
        with patch.object(self.client, "_fetch", return_value=page), patch.object(self.client.github, "find", return_value=None):
            results = self.client.check_mods([self.hub.mods["FS25_New.zip"]])
        self.assertEqual(results[0]["status"], "No exact match")
        self.assertFalse(results[0]["can_update"])

    def test_update_keeps_exact_old_zip_and_adds_dependencies_to_affected_loadouts(self):
        unrelated = self.hub.new_loadout("Other", ["FS25_Map.zip"])
        untouched = copy.deepcopy(unrelated)
        result = self.client.install_update(self.hub, "FS25_Existing.zip")
        self.assertEqual(self.source.read_bytes(), self.updated.read_bytes())
        self.assertEqual((result["backup"] / self.source.name).read_bytes(), self.old_bytes)
        self.assertEqual(self.hub.mods[self.source.name]["version"], "2.0.0.0")
        self.assertTrue({"FS25_New.zip", "FS25_Dep.zip"} <= set(self.loadout["mods"]))
        self.assertEqual(unrelated, untouched)
        self.assertEqual(self.client.results(self.hub.mods)[0]["status"], "Current")
        stored = json.loads(self.hub.config_path.read_text())
        self.assertIn("FS25_Dep.zip", next(l for l in stored["loadouts"] if l["id"] == self.loadout["id"])["mods"])

    def test_running_game_blocks_download_and_replacement(self):
        with patch.object(core, "game_running", return_value=True):
            with self.assertRaises(HubError):
                self.client.install_update(self.hub, self.source.name)
        self.client._download.assert_not_called()
        self.assertEqual(self.source.read_bytes(), self.old_bytes)

    def test_game_started_during_download_keeps_old_file(self):
        with patch.object(core, "game_running", side_effect=[False, True]):
            with self.assertRaises(HubError):
                self.client.install_update(self.hub, self.source.name)
        self.assertEqual(self.source.read_bytes(), self.old_bytes)

    def test_corrupt_or_wrong_version_download_never_replaces_installed_file(self):
        self.updated.write_bytes(b"Interrupted or non-ZIP download")
        with self.assertRaises(HubError):
            self.client.install_update(self.hub, self.source.name)
        self.assertEqual(self.source.read_bytes(), self.old_bytes)
        make_mod(self.updated, version="1.9.0.0")
        with self.assertRaisesRegex(HubError, "did not match"):
            self.client.install_update(self.hub, self.source.name)
        self.assertEqual(self.source.read_bytes(), self.old_bytes)

    def test_new_missing_dependency_keeps_original_mod(self):
        make_mod(self.updated, version="2.0.0.0", deps=["FS25_NotInstalled"])
        with self.assertRaisesRegex(HubError, "Import them first"):
            self.client.install_update(self.hub, self.source.name)
        self.assertEqual(self.source.read_bytes(), self.old_bytes)

    def test_config_write_failure_restores_original_mod_and_selections(self):
        before = copy.deepcopy(self.hub.config)
        with patch.object(self.hub, "save_config", side_effect=OSError("Disk full")):
            with self.assertRaises(OSError):
                self.client.install_update(self.hub, self.source.name)
        self.assertEqual(self.source.read_bytes(), self.old_bytes)
        self.assertEqual(self.hub.config, before)
        self.assertTrue(list((self.hub.data / "mod-backups").glob("*/FS25_Existing.zip")))

    def test_file_changed_by_another_program_is_kept(self):
        def download(url, dest, progress=None):
            shutil.copy2(self.updated, dest)
            make_mod(self.source, version="1.5.0.0")
        with patch.object(self.client, "_download", side_effect=download):
            with self.assertRaisesRegex(HubError, "changed during download"):
                self.client.install_update(self.hub, self.source.name)
        self.assertEqual(core.mod_info(self.source)["version"], "1.5.0.0")

    def test_partial_download_is_rejected(self):
        class Response(io.BytesIO):
            headers = {"Content-Length": "100"}
        with patch.object(self.client, "_open", return_value=Response(b"short")):
            with self.assertRaisesRegex(HubError, "incomplete"):
                ModHubClient._download(self.client, self.entry["download_url"], self.home / "partial.zip")


if __name__ == "__main__":
    unittest.main()
