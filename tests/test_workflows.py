import copy
import io
import json
import unittest
import xml.etree.ElementTree as ET
import zipfile
from unittest.mock import patch

from farmsim_hub.core import Hub, HubError
from farmsim_hub.transfers import export_loadout
from tests import test_core as fixtures


class TemporaryHub:
    def setUp(self):
        self.fixture = fixtures.CoreTests()
        self.fixture.setUp()
        for name in ["hub", "home", "library", "loadout", "career", "settings", "save", "profile"]:
            setattr(self, name, getattr(self.fixture, name))

    def tearDown(self):
        self.fixture.tearDown()

    def install_fixture_dlcs(self):
        folder = self.home / ".local/share/Steam/steamapps/common/Farming Simulator 25/pdlc"
        folder.mkdir(parents=True)
        for name in ["testPack", "newPack"]:
            (folder / (name + ".dlc")).write_bytes(b"Fixture paid archive; never exported")
        (self.profile / "log.txt").write_text(
            "Available dlc: (Hash: 11111111111111111111111111111111) (Version: 2.0.0.0) pdlc_testPack\n"
            "Available dlc: (Hash: 22222222222222222222222222222222) (Version: 1.5.0.0) pdlc_newPack\n")


class WorkflowTests(TemporaryHub, unittest.TestCase):
    def test_edit_collection_persists_membership_without_touching_other_data(self):
        collection = {"name": "Equipment", "mods": ["FS25_Existing.zip", "FS25_Missing.zip"], "color": "#abcdef"}
        duplicate = copy.deepcopy(collection)
        self.hub.config["collections"] = [collection, duplicate]
        loadouts = copy.deepcopy(self.hub.config["loadouts"])
        files = {p: p.read_bytes() for p in [*self.library.glob("*.zip"), self.career, self.settings]}
        self.hub.set_collection_mods(collection, ["FS25_New.zip", "FS25_New.zip", "FS25_Missing.zip"])
        self.assertEqual(collection["mods"], ["FS25_Missing.zip", "FS25_New.zip"])
        self.assertEqual(duplicate["mods"], ["FS25_Existing.zip", "FS25_Missing.zip"])
        self.assertEqual(self.hub.config["loadouts"], loadouts)
        self.assertEqual(Hub(home=self.home, data_dir=self.hub.data).config["collections"][0], collection)
        for path, before in files.items():
            self.assertEqual(path.read_bytes(), before)
        # Membership does not automatically add dependencies or force a map.
        self.assertNotIn("FS25_Dep.zip", collection["mods"])
        self.hub.set_collection_mods(collection, [])
        self.assertEqual(Hub(home=self.home, data_dir=self.hub.data).config["collections"][0]["mods"], [])

    def test_failed_collection_edit_keeps_memory_and_saved_membership(self):
        collection = {"name": "Equipment", "mods": ["FS25_Existing.zip"], "color": "#abcdef"}
        self.hub.config["collections"] = [collection]
        self.hub.save_config()
        before = copy.deepcopy(self.hub.config)
        with patch.object(self.hub, "save_config", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.hub.set_collection_mods(collection, ["FS25_New.zip"])
        self.assertEqual(self.hub.config, before)
        self.assertIs(self.hub.config["collections"][0], collection)
        self.assertEqual(Hub(home=self.home, data_dir=self.hub.data).config, before)

    def test_collection_edit_rejects_deleted_collection_or_new_missing_mod(self):
        collection = {"name": "Equipment", "mods": [], "color": "#abcdef"}
        with self.assertRaisesRegex(HubError, "no longer exists"):
            self.hub.set_collection_mods(collection, ["FS25_New.zip"])
        self.hub.config["collections"] = [collection]
        with self.assertRaisesRegex(HubError, "no longer in the library"):
            self.hub.set_collection_mods(collection, ["FS25_NotInstalled.zip"])
        self.assertEqual(collection["mods"], [])

    def test_delete_one_duplicate_collection_preserves_mods_and_loadouts(self):
        collection = {"name": "Same name", "mods": ["FS25_New.zip"], "color": "#abcdef"}
        duplicate = copy.deepcopy(collection)
        self.hub.config["collections"] = [collection, duplicate]
        before = copy.deepcopy(self.hub.config["loadouts"])
        self.assertTrue(self.hub.delete_collection(duplicate))
        self.assertIs(self.hub.config["collections"][0], collection)
        self.assertEqual(self.hub.config["loadouts"], before)
        self.assertTrue((self.library / "FS25_New.zip").exists())
        self.assertEqual(len(Hub(home=self.home, data_dir=self.hub.data).config["collections"]), 1)

    def test_deleted_detected_loadouts_do_not_reappear_and_game_files_are_kept(self):
        before = {p: p.read_bytes() for p in [self.settings, self.career]}
        deleted_ids = {l["id"] for l in self.hub.loadouts()}
        for loadout in self.hub.loadouts():
            self.hub.delete_loadout(loadout)
        reloaded = Hub(home=self.home, data_dir=self.hub.data)
        reloaded.scan()
        self.assertFalse(reloaded.loadouts())
        self.assertFalse(deleted_ids & {l["id"] for l in reloaded.loadouts()})
        self.assertTrue(self.fixture.old_profile.is_dir())
        for path, content in before.items():
            self.assertEqual(path.read_bytes(), content)

    def test_failed_delete_keeps_the_collection_and_loadout(self):
        collection = {"name": "Keep", "mods": [], "color": "#ffffff"}
        self.hub.config["collections"] = [collection]
        before = copy.deepcopy(self.hub.config)
        with patch.object(self.hub, "save_config", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.hub.delete_collection(collection)
            with self.assertRaises(OSError):
                self.hub.delete_loadout(self.loadout)
        self.assertEqual(self.hub.config, before)
        self.assertIs(self.hub.config["collections"][0], collection)

    def test_export_contains_actual_zips_map_dependencies_and_dlc_manifest(self):
        self.loadout["mods"] = ["FS25_New.zip"]
        self.loadout["dlcs"] = ["pdlc_testPack"]
        output = self.home / "server-bundle.zip"
        result = export_loadout(self.hub, self.loadout, output)
        self.assertEqual(result["mods"], 3)
        with zipfile.ZipFile(output) as archive:
            self.assertEqual(set(archive.namelist()), {"mods/FS25_New.zip", "mods/FS25_Dep.zip", "mods/FS25_Map.zip", "loadout.json", "README.txt"})
            for name in ["FS25_New.zip", "FS25_Dep.zip", "FS25_Map.zip"]:
                self.assertEqual(archive.read("mods/" + name), (self.library / name).read_bytes())
            manifest = json.loads(archive.read("loadout.json"))
            self.assertEqual(manifest["dlcs"], ["pdlc_testPack"])
            self.assertNotIn("savegame1/careerSavegame.xml", archive.namelist())
        self.assertEqual(self.loadout["mods"], ["FS25_New.zip"])

    def test_export_packs_an_unpacked_mod_with_moddesc_at_the_zip_root(self):
        mod = self.library / "FS25_Unpacked"
        mod.mkdir()
        (mod / "modDesc.xml").write_text('<modDesc><title><en>Unpacked</en></title><version>1.0.0.0</version></modDesc>')
        (mod / "textures").mkdir()
        (mod / "textures/icon.dds").write_bytes(b"Fixture artwork")
        self.hub.scan()
        loadout = self.hub.new_loadout("Unpacked server", [mod.name])
        output = self.home / "unpacked.zip"
        export_loadout(self.hub, loadout, output)
        with zipfile.ZipFile(output) as bundle, zipfile.ZipFile(io.BytesIO(bundle.read("mods/FS25_Unpacked.zip"))) as packed:
            self.assertEqual(set(packed.namelist()), {"modDesc.xml", "textures/icon.dds"})

    def test_export_missing_dependency_does_not_produce_a_partial_bundle(self):
        (self.library / "FS25_Dep.zip").unlink()
        self.hub.scan()
        loadout = self.hub.new_loadout("Missing", ["FS25_New.zip"])
        output = self.home / "missing.zip"
        with self.assertRaisesRegex(HubError, "Missing mods or dependencies"):
            export_loadout(self.hub, loadout, output)
        self.assertFalse(output.exists())

    def test_export_failure_keeps_an_existing_export_and_cleans_temporary_files(self):
        mod = self.library / "FS25_Unpacked"
        mod.mkdir()
        (mod / "modDesc.xml").write_text('<modDesc><version>1.0.0.0</version></modDesc>')
        (mod / "outside.xml").symlink_to(self.career)
        self.hub.scan()
        loadout = self.hub.new_loadout("Linked", [mod.name])
        output = self.home / "previous.zip"
        output.write_bytes(b"Existing export")
        with self.assertRaises(HubError):
            export_loadout(self.hub, loadout, output)
        self.assertEqual(output.read_bytes(), b"Existing export")
        self.assertFalse(list(self.home.glob(".hub-export-*")))

    def test_export_cannot_overwrite_a_source_mod(self):
        before = (self.library / "FS25_Map.zip").read_bytes()
        with self.assertRaises(HubError):
            export_loadout(self.hub, self.loadout, self.library / "FS25_Map.zip")
        self.assertEqual((self.library / "FS25_Map.zip").read_bytes(), before)

    def test_export_also_protects_mods_outside_the_exported_loadout(self):
        source = self.library / "FS25_New.zip"
        self.assertNotIn(source.name, self.loadout["mods"])
        before = source.read_bytes()
        with self.assertRaises(HubError):
            export_loadout(self.hub, self.loadout, source)
        self.assertEqual(source.read_bytes(), before)

    def test_dlc_choices_add_and_remove_selection_and_full_backup_keeps_old_state(self):
        self.install_fixture_dlcs()
        before = self.career.read_bytes()
        self.hub.set_loadout_dlcs(self.loadout, ["pdlc_newPack"], 1)
        backup = self.hub.apply(self.loadout)
        root = ET.parse(self.career).getroot()
        dlcs = [m for m in root.findall("mod") if m.get("modName", "").startswith("pdlc_")]
        self.assertEqual([m.get("modName") for m in dlcs], ["pdlc_newPack"])
        self.assertEqual(dlcs[0].get("version"), "1.5.0.0")
        self.assertEqual(dlcs[0].get("fileHash"), "2" * 32)
        self.assertIsNotNone(root.find("unknownModData"))
        with zipfile.ZipFile(backup) as archive:
            self.assertEqual(archive.read("savegame1/careerSavegame.xml"), before)

    def test_dlc_map_cannot_be_deselected(self):
        self.install_fixture_dlcs()
        root = ET.parse(self.career).getroot()
        root.find("settings/mapId").text = "pdlc_testPack.MapDLC"
        self.career.write_bytes(ET.tostring(root))
        self.hub.scan()
        self.hub.set_loadout_dlcs(self.loadout, [], 1)
        self.assertEqual(self.loadout["dlcs"], ["pdlc_testPack"])
        self.loadout["dlcs"] = []
        with self.assertRaisesRegex(HubError, "DLC map"):
            self.hub.validate_loadout(self.loadout)

    def test_missing_dlc_is_rejected_without_changing_saved_choices(self):
        before = copy.deepcopy(self.loadout)
        with self.assertRaisesRegex(HubError, "Install these DLCs"):
            self.hub.set_loadout_dlcs(self.loadout, ["pdlc_missing"], 1)
        self.assertEqual(self.loadout, before)

    def test_linking_a_loadout_to_a_save_adds_that_saves_map(self):
        loadout = self.hub.new_loadout("Independent", ["FS25_Existing.zip"])
        self.hub.set_loadout_dlcs(loadout, [], 1)
        self.assertIn("FS25_Map.zip", loadout["mods"])
        self.assertEqual(loadout["slot"], 1)


if __name__ == "__main__":
    unittest.main()
