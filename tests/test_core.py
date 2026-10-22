import json
import tempfile
import unittest
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from unittest.mock import patch

from farmsim_hub import core
from farmsim_hub.core import Hub, HubError, game_running, mod_info, read_xml


def make_mod(path, title="Test mod", deps=(), is_map=False, version="1.0.0.0"):
    root = ET.Element("modDesc", descVersion="105")
    ET.SubElement(ET.SubElement(root, "title"), "en").text = title
    ET.SubElement(root, "version").text = version
    ET.SubElement(root, "author").text = "Fixture author"
    if deps:
        el = ET.SubElement(root, "dependencies")
        for dep in deps:
            ET.SubElement(el, "dependency").text = dep
    if is_map:
        ET.SubElement(ET.SubElement(root, "maps"), "map", id="SampleModMap")
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("modDesc.xml", ET.tostring(root))
        archive.writestr("content.txt", title)


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        self.profile = self.home / (".local/share/Steam/steamapps/compatdata/2300320/" + str(core.PROFILE_SUFFIX))
        self.profile.mkdir(parents=True)
        prefix = next(p for p in self.profile.parents if p.name == "pfx")
        (prefix / "dosdevices").mkdir()
        (prefix / "dosdevices/z:").symlink_to("/")
        self.settings = self.profile / "gameSettings.xml"
        self.settings.write_text('<gameSettings><modsDirectoryOverride active="false" directory="C:/Temp"/>'
                                 '<unrelated keep="yes">keep me</unrelated></gameSettings>')
        self.save = self.profile / "savegame1"
        self.save.mkdir()
        self.career = self.save / "careerSavegame.xml"
        self.career.write_text('''<careerSavegame valid="true"><settings>
          <savegameName>Original farm</savegameName><mapTitle>Fixture map</mapTitle>
          <mapId>FS25_Map.SampleModMap</mapId><economicDifficulty>NORMAL</economicDifficulty>
          <trafficEnabled>true</trafficEnabled><plannedDaysPerPeriod>1</plannedDaysPerPeriod>
          <timeScale>5</timeScale><customModSetting>preserve this</customModSetting></settings>
          <mod modName="FS25_Map" title="Map" version="1.0.0.0" required="true" fileHash="maphash"/>
          <mod modName="FS25_Existing" title="Existing" version="1.0.0.0" required="false" fileHash="existinghash"/>
          <mod modName="pdlc_testPack" title="Paid content" version="1" fileHash="dlchash" required="true"/>
          <unknownModData enabled="true"/></careerSavegame>''')
        (self.save / "vehicles.xml").write_text("<vehicles>original vehicles</vehicles>")
        self.library = self.home / "Documents/FS25ModLibrary"
        self.library.mkdir(parents=True)
        make_mod(self.library / "FS25_Map.zip", "Fixture map", is_map=True)
        make_mod(self.library / "FS25_Existing.zip", "Existing")
        make_mod(self.library / "FS25_New.zip", "New mod", deps=["FS25_Dep"])
        make_mod(self.library / "FS25_Dep.zip", "Dependency", deps=["FS25_New"])
        self.old_profile = self.home / "Documents/FS25ModProfiles/Default"
        self.old_profile.mkdir(parents=True)
        for path in self.library.glob("*.zip"):
            (self.old_profile / path.name).symlink_to(path)
        (self.profile / "mods").symlink_to(self.old_profile)
        self.hub = Hub(home=self.home, data_dir=self.home / "app-data")
        self.guard = patch.object(core, "game_running", return_value=False)
        self.guard.start()
        self.hub.scan()
        self.loadout = next(l for l in self.hub.loadouts() if l.get("slot") == 1)

    def tearDown(self):
        self.guard.stop()
        self.temp.cleanup()

    def test_scan_never_changes_game_or_existing_switcher(self):
        before = {p: p.read_bytes() for p in [self.settings, self.career]}
        self.hub.scan()
        self.assertEqual(len(self.hub.mods), 4)
        self.assertEqual(len(self.hub.saves), 1)
        self.assertEqual(len(self.hub.loadouts()), 2)
        self.assertEqual((self.profile / "mods").resolve(), self.old_profile)
        for path, content in before.items():
            self.assertEqual(path.read_bytes(), content)

    def test_metadata_cache_from_previous_version_is_upgraded_with_icon_field(self):
        cache = json.loads(self.hub.cache_path.read_text())
        for item in cache.values():
            item.pop("icon", None)
        self.hub.cache_path.write_text(json.dumps(cache))
        self.hub.scan()
        self.assertTrue(all("icon" in item for item in self.hub.mods.values()))

    def test_dependency_cycles_resolve_and_map_cannot_be_deselected(self):
        selected, missing = self.hub.dependency_closure(["FS25_New.zip"])
        self.assertEqual(selected, {"FS25_New.zip", "FS25_Dep.zip"})
        self.assertFalse(missing)
        self.hub.set_selection(self.loadout, ["FS25_New.zip"])
        self.assertIn("FS25_Map.zip", self.loadout["mods"])

    def test_apply_writes_override_and_save_selection_preserving_dlc_and_xml(self):
        settings_before, career_before = self.settings.read_bytes(), self.career.read_bytes()
        self.hub.set_selection(self.loadout, ["FS25_New.zip"])
        backup = self.hub.apply(self.loadout)
        root = ET.parse(self.settings).getroot()
        override = root.find("modsDirectoryOverride")
        self.assertEqual(override.get("active"), "true")
        self.assertTrue(override.get("directory").startswith("Z:/"))
        self.assertEqual(root.findtext("unrelated"), "keep me")
        career = ET.parse(self.career).getroot()
        mods = {m.get("modName"): m for m in career.findall("mod")}
        self.assertEqual(set(mods), {"FS25_Map", "FS25_New", "FS25_Dep", "pdlc_testPack"})
        self.assertEqual(mods["pdlc_testPack"].get("fileHash"), "dlchash")
        self.assertEqual(len(mods["FS25_New"].get("fileHash")), 32)
        self.assertIsNotNone(career.find("unknownModData"))
        applied = Path(self.hub.config["last_applied"]["directory"])
        self.assertEqual({p.name for p in applied.iterdir()}, set(self.loadout["mods"]))
        self.assertTrue(all(p.is_symlink() for p in applied.iterdir()))
        self.assertEqual((self.profile / "mods").resolve(), self.old_profile)
        with zipfile.ZipFile(backup) as archive:
            self.assertEqual(archive.read("gameSettings.xml"), settings_before)
            self.assertEqual(archive.read("savegame1/careerSavegame.xml"), career_before)
            self.assertIn("savegame1/vehicles.xml", archive.namelist())

    def test_apply_failure_rolls_back_career_and_keeps_backup(self):
        before = self.career.read_bytes()
        settings_before = self.settings.read_bytes()
        self.hub.set_selection(self.loadout, ["FS25_New.zip"])
        atomic = core.atomic_write

        def fail_settings(path, data):
            if path == self.settings:
                raise OSError("simulated disk failure")
            return atomic(path, data)

        with patch.object(core, "atomic_write", side_effect=fail_settings):
            with self.assertRaises(OSError):
                self.hub.apply(self.loadout)
        self.assertEqual(self.career.read_bytes(), before)
        self.assertEqual(self.settings.read_bytes(), settings_before)
        self.assertTrue(self.hub.backups())

    def test_changed_mod_version_gets_a_fresh_hash(self):
        make_mod(self.library / "FS25_Existing.zip", "Updated mod", version="2.0.0.0")
        self.hub.scan()
        self.hub.apply(self.loadout)
        mod = next(m for m in ET.parse(self.career).getroot().findall("mod") if m.get("modName") == "FS25_Existing")
        expected = core.hashlib.md5((self.library / "FS25_Existing.zip").read_bytes()).hexdigest()
        self.assertEqual(mod.get("version"), "2.0.0.0")
        self.assertEqual(mod.get("fileHash"), expected)

    def test_uppercase_zip_extension_keeps_correct_game_mod_name(self):
        make_mod(self.library / "FS25_Case.ZIP")
        self.hub.scan()
        self.assertFalse(self.hub.mods["FS25_Case.ZIP"]["error"])
        self.hub.set_selection(self.loadout, ["FS25_Case.ZIP"])
        self.hub.apply(self.loadout)
        names = {m.get("modName") for m in ET.parse(self.career).getroot().findall("mod")}
        self.assertIn("FS25_Case", names)
        self.assertNotIn("FS25_Case.ZIP", names)

    def test_running_game_blocks_mutations(self):
        before = self.settings.read_bytes()
        with patch.object(core, "game_running", return_value=True):
            for action in [lambda: self.hub.apply(self.loadout),
                           lambda: self.hub.save_settings(1, {"trafficEnabled": "false"}),
                           lambda: self.hub.clone_save(1, 2, "Copy"), lambda: self.hub.backup(1)]:
                with self.assertRaises(HubError):
                    action()
        self.assertEqual(self.settings.read_bytes(), before)
        self.assertFalse(self.hub.backups())

    def test_missing_dependency_blocks_apply_before_game_changes(self):
        self.loadout["mods"].append("FS25_Missing.zip")
        before = self.settings.read_bytes()
        with self.assertRaisesRegex(HubError, "Missing"):
            self.hub.apply(self.loadout)
        self.assertEqual(before, self.settings.read_bytes())
        self.assertFalse(self.hub.backups())

    def test_save_settings_validate_and_preserve_mod_data(self):
        backup = self.hub.save_settings(1, {"trafficEnabled": "false", "economicDifficulty": "HARD", "savegameName": "A & B"})
        root = ET.parse(self.career).getroot()
        self.assertEqual(root.findtext("settings/savegameName"), "A & B")
        self.assertEqual(root.findtext("settings/trafficEnabled"), "false")
        self.assertEqual(root.findtext("settings/customModSetting"), "preserve this")
        self.assertEqual(len(root.findall("mod")), 3)
        self.assertTrue(backup.exists())
        before = self.career.read_bytes()
        for changes in [{"plannedDaysPerPeriod": "0"}, {"customModSetting": "changed"}, {"timeScale": "nan"}]:
            with self.assertRaises(HubError):
                self.hub.save_settings(1, changes)
        self.assertEqual(self.career.read_bytes(), before)

    def test_full_restore_recovers_every_file_and_creates_recovery_backup(self):
        before_settings, before_career = self.settings.read_bytes(), self.career.read_bytes()
        backup = self.hub.backup(1)
        self.hub.save_settings(1, {"trafficEnabled": "false"})
        (self.save / "vehicles.xml").write_text("changed")
        (self.save / "new-file.txt").write_text("after backup")
        self.hub.restore(backup)
        self.assertEqual(self.settings.read_bytes(), before_settings)
        self.assertEqual(self.career.read_bytes(), before_career)
        self.assertEqual((self.save / "vehicles.xml").read_text(), "<vehicles>original vehicles</vehicles>")
        self.assertFalse((self.save / "new-file.txt").exists())
        self.assertTrue(any(b["reason"] == "Before restoring a backup" for b in self.hub.backups()))

    def test_restore_rejects_path_traversal(self):
        backup = self.hub.backup(1)
        with zipfile.ZipFile(backup, "a") as archive:
            archive.writestr("savegame1/../../escape.txt", "unsafe")
        before = self.career.read_bytes()
        with self.assertRaisesRegex(HubError, "Unsafe"):
            self.hub.restore(backup)
        self.assertEqual(self.career.read_bytes(), before)
        self.assertFalse((self.profile.parent / "escape.txt").exists())

    def test_clone_copies_playable_save_to_empty_slot_and_never_overwrites(self):
        self.hub.clone_save(1, 2, "New farm")
        copy = self.profile / "savegame2"
        self.assertEqual(ET.parse(copy / "careerSavegame.xml").getroot().findtext("settings/savegameName"), "New farm")
        self.assertEqual((copy / "vehicles.xml").read_bytes(), (self.save / "vehicles.xml").read_bytes())
        with self.assertRaises(HubError):
            self.hub.clone_save(1, 2, "Overwrite")

    def test_import_copies_valid_mods_without_overwriting(self):
        downloads = self.home / "Downloads"
        downloads.mkdir()
        new = downloads / "FS25_Import.zip"
        make_mod(new)
        before = new.read_bytes()
        names, skipped = self.hub.import_mods([new])
        self.assertEqual(names, [new.name])
        self.assertFalse(skipped)
        self.assertEqual(new.read_bytes(), before)
        make_mod(new, "Different content")
        names, skipped = self.hub.import_mods([new])
        self.assertFalse(names)
        self.assertTrue(skipped)
        self.assertEqual((self.hub.data / "imports" / new.name).read_bytes(), before)

    def test_bad_zip_and_xml_entity_are_not_loaded(self):
        bad = self.library / "FS25_Bad.zip"
        bad.write_text("not a zip")
        self.assertTrue(mod_info(bad)["error"])
        with self.assertRaises(HubError):
            read_xml(b'<!DOCTYPE x [<!ENTITY a "abc">]><x>&a;</x>')

    def test_steam_launch_is_an_argument_list_and_uses_save_slot(self):
        with patch.object(core.shutil, "which", return_value="/usr/bin/steam"):
            self.assertEqual(Hub.launch_command(3), ["/usr/bin/steam", "-applaunch", "2300320", "-autoStartSavegameId", "3"])
        with patch.object(core.subprocess, "Popen") as start:
            Hub.launch(1)
            self.assertEqual(start.call_args.args[0][-2:], ["-autoStartSavegameId", "1"])

    def test_process_detection_does_not_match_arbitrary_text_in_shell_arguments(self):
        proc = self.home / "proc"
        (proc / "123").mkdir(parents=True)
        cmdline = proc / "123/cmdline"
        cmdline.write_bytes(b"bash\0-c\0echo FarmingSimulator2025Game.exe something\0")
        self.guard.stop()
        try:
            self.assertFalse(game_running(proc))
            cmdline.write_bytes(b"Z:/games/FarmingSimulator2025Game.exe\0-restart\0")
            self.assertTrue(game_running(proc))
        finally:
            self.guard.start()


if __name__ == "__main__":
    unittest.main()
