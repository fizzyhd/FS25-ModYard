import json
import unittest
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from unittest.mock import patch

from farmsim_hub.core import mod_info
from farmsim_hub.categories import matches_search, read_mod_categories
from farmsim_hub.modhub import ModHubClient, parse_mod_page
from tests.test_workflows import TemporaryHub


def store(category):
    return '<vehicle><storeData><category>' + category + '</category></storeData></vehicle>'


def pack(path, files, refs=None, extra=""):
    root = ET.fromstring('<modDesc><title><en>Model 417</en></title><author>Fixture</author><version>1.0.0.0</version>' + extra + '</modDesc>')
    items = ET.SubElement(root, "storeItems")
    for name in refs if refs is not None else files:
        ET.SubElement(items, "storeItem", xmlFilename=name)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("modDesc.xml", ET.tostring(root))
        for name, data in files.items():
            archive.writestr(name, data)


class CategoryTests(TemporaryHub, unittest.TestCase):
    def test_category_search_finds_models_without_category_in_their_name(self):
        path = self.library / "FS25_Model417.zip"
        pack(path, {"vehicle.xml": store("teleLoaderVehicles")})
        info = mod_info(path)
        self.assertEqual(info["categories"], ["Telehandlers"])
        for query in ["telehandler", "Telehandlers", "tele handler", "teleLoaderVehicles"]:
            self.assertTrue(matches_search(info, query), query)
        self.assertFalse(matches_search(info, "combine"))

    def test_mixed_pack_has_all_registered_categories_and_excludes_unused_xml(self):
        path = self.library / "FS25_Pack.zip"
        pack(path, {"combine.xml": store("harvesters"), "header.xml": store("cutters"), "unused.xml": store("teleLoaderVehicles")}, refs=["combine.xml", "header.xml"])
        info = mod_info(path)
        self.assertEqual(info["categories"], ["Combine harvesters", "Grain headers"])
        self.assertTrue(matches_search(info, "header"))
        self.assertTrue(matches_search(info, "combine"))
        self.assertFalse(matches_search(info, "telehandler"))

    def test_multiple_categories_and_windows_paths_are_supported(self):
        path = self.library / "FS25_Tools.zip"
        pack(path, {"Tools/Fork.XML": store("frontLoaderTools teleLoaderTools wheelLoaderTools")}, refs=["tools\\fork.xml"])
        self.assertEqual(mod_info(path)["categories"], ["Front loader tools", "Telehandler tools", "Wheel loader tools"])

    def test_unpacked_item_category_changes_invalidate_the_cache(self):
        path = self.library / "FS25_Model417"
        path.mkdir()
        (path / "modDesc.xml").write_text('<modDesc><title><en>Model 417</en></title><storeItems><storeItem xmlFilename="item.xml"/></storeItems></modDesc>')
        item = path / "item.xml"
        item.write_text(store("teleLoaderVehicles"))
        self.hub.scan()
        self.assertEqual(self.hub.mods[path.name]["categories"], ["Telehandlers"])
        item.write_text(store("wheelLoaderVehicles"))
        self.hub.scan()
        self.assertEqual(self.hub.mods[path.name]["categories"], ["Wheel loaders"])

    def test_parent_xml_is_followed_within_the_mod(self):
        path = self.library / "FS25_Parent.zip"
        pack(path, {"vehicles/item.xml": '<vehicle><parentFile xmlFilename="../base.xml"/></vehicle>', "base.xml": store("teleLoaderVehicles")}, refs=["vehicles/item.xml"])
        self.assertEqual(mod_info(path)["categories"], ["Telehandlers"])

    def test_base_game_parent_xml_can_supply_the_category(self):
        data = self.home / "game/data"
        (data / "vehicles").mkdir(parents=True)
        (data / "vehicles/base.xml").write_text(store("teleLoaderVehicles"))
        path = self.library / "FS25_Parent.zip"
        pack(path, {"item.xml": '<vehicle><parentFile xmlFilename="$data/vehicles/base.xml"/></vehicle>'})
        self.assertEqual(mod_info(path, [data])["categories"], ["Telehandlers"])

    def test_paths_and_symlinks_cannot_read_outside_the_mod(self):
        outside = self.home / "outside.xml"
        outside.write_text(store("teleLoaderVehicles"))
        path = self.library / "FS25_Unsafe"
        path.mkdir()
        (path / "modDesc.xml").write_text('<modDesc><storeItems><storeItem xmlFilename="../../outside.xml"/><storeItem xmlFilename="linked.xml"/></storeItems></modDesc>')
        (path / "linked.xml").symlink_to(outside)
        self.assertEqual(mod_info(path)["categories"], ["Equipment"])

    def test_unknown_and_custom_categories_get_readable_labels(self):
        path = self.library / "FS25_Custom.zip"
        extra = '<l10n><text name="shopCat"><en>Farm workshops</en></text></l10n><storeCategories><storeCategory name="customShop" title="$l10n_shopCat"/></storeCategories>'
        pack(path, {"item.xml": store("customShop customMagicTools")}, extra=extra)
        self.assertEqual(mod_info(path)["categories"], ["Farm workshops", "Custom magic tools"])

    def test_bad_store_xml_does_not_mark_a_readable_mod_broken(self):
        path = self.library / "FS25_BadStore.zip"
        pack(path, {"item.xml": '<!DOCTYPE vehicle [<!ENTITY x "teleLoaderVehicles">]><vehicle><storeData><category>&x;</category></storeData></vehicle>'})
        info = mod_info(path)
        self.assertFalse(info["error"])
        self.assertEqual(info["categories"], ["Equipment"])

    def test_older_metadata_cache_is_upgraded_with_categories(self):
        cache = json.loads(self.hub.cache_path.read_text())
        for item in cache.values():
            item.pop("category_schema", None)
            item.pop("categories", None)
        self.hub.cache_path.write_text(json.dumps(cache))
        self.hub.scan()
        self.assertTrue(all(m.get("categories") for m in self.hub.mods.values()))

    def test_modhub_telehandler_search_uses_the_real_category(self):
        client = ModHubClient(self.hub.data)
        with patch.object(client, "_fetch", return_value="<html>No results</html>") as fetch:
            result = client.search("telehandler")
        self.assertEqual(result["category"], "teleLoaderVehicles")
        self.assertIn("filter=teleLoaderVehicles", fetch.call_args.args[0])
        self.assertNotIn("searchMod=", fetch.call_args.args[0])

    def test_modhub_category_metadata_is_shown_and_cached(self):
        client = ModHubClient(self.hub.data)
        search = '<div class="mod-item"><a href="mod.php?mod_id=123456&amp;title=fs2025">Info</a><h4>Model 417</h4></div>'
        detail = {"category": "Telehandlers", "filename": "FS25_Model417.zip", "version": "1.0.0.0", "checked_at": "2099-01-01T00:00:00+00:00"}
        with patch.object(client, "_fetch", return_value=search), patch.object(client, "detail", return_value=detail) as read:
            first = client.search("Model 417")
            second = client.search("Model 417")
        self.assertEqual(first["items"][0]["category"], "Telehandlers")
        self.assertEqual(second["items"][0]["category"], "Telehandlers")
        read.assert_called_once()

    def test_modhub_mixed_pack_keeps_its_primary_and_selected_categories(self):
        client = ModHubClient(self.hub.data)
        search = '<div class="mod-item"><a href="mod.php?mod_id=123456&amp;title=fs2025">Info</a><h4>Loader pack</h4></div>'
        detail = {"category": "Front loader vehicles", "filename": "FS25_Loaders.zip", "version": "1.0.0.0", "checked_at": "2099-01-01T00:00:00+00:00"}
        with patch.object(client, "_fetch", return_value=search), patch.object(client, "detail", return_value=detail):
            result = client.search("telehandler")
        self.assertEqual(result["items"][0]["categories"], ["Front loader vehicles", "Telehandlers"])


if __name__ == "__main__":
    unittest.main()
