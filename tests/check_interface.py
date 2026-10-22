"""Exercise the native card browser and render it without desktop input."""

import json
import copy
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from farmsim_hub.app import Gtk, Window
from farmsim_hub.core import Hub
from farmsim_hub.pictures import ModPictures
from tests.test_core import make_mod


def pump():
    for _ in range(8):
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)
        time.sleep(0.01)


def find_buttons(widget, title):
    found = [widget] if isinstance(widget, Gtk.Button) and widget.get_label() == title else []
    if isinstance(widget, Gtk.Container):
        for child in widget.get_children():
            found.extend(find_buttons(child, title))
    return found


def find_widgets(widget, kind):
    found = [widget] if isinstance(widget, kind) else []
    if isinstance(widget, Gtk.Container):
        for child in widget.get_children():
            found.extend(find_widgets(child, kind))
    return found


def main():
    assert Gtk.init_check()[0], "A display connection is needed for GTK offscreen rendering"
    with tempfile.TemporaryDirectory() as directory:
        hub = Hub(data_dir=Path(directory))
        hub.scan()
        window = Window(hub, auto_scan=False, visible=False)
        # Reuse local image caches while keeping all configuration disposable.
        window.pictures = ModPictures(Hub().data / "thumbnails")
        window.pictures.prepare(hub.mods)
        expected_pictures = {n for n, m in hub.mods.items() if m.get("icon") and not m["error"]}
        assert expected_pictures <= set(window.pictures.images), window.pictures.failures
        window.loaded()
        contents = window.get_child()
        window.remove(contents)
        window.stack.set_transition_type(Gtk.StackTransitionType.NONE)
        offscreen = Gtk.OffscreenWindow()
        offscreen.set_size_request(1360, 850)
        offscreen.add(contents)
        offscreen.show_all()
        pump()
        assert len(window.library.cards) == len(window.model)
        assert window.current is not None and hub.saves
        telehandlers = {n for n, m in hub.mods.items() if "Telehandlers" in m.get("categories", [])}
        if telehandlers:
            window.search.set_text("telehandler")
            window._filter_changed()
            assert telehandlers <= window.library.visible_names
            assert any("telehandler" not in (hub.mods[n]["title"] + n).lower() for n in telehandlers)
            window.library.filter_to("Telehandlers")
            assert window.library.visible_names == telehandlers
            assert all("Telehandlers" in window.library.cards[n].category_badge.get_tooltip_text() for n in telehandlers)
            pump()
            category_output = Path("/tmp/farmsim-hub-interface")
            category_output.mkdir(exist_ok=True)
            offscreen.get_pixbuf().savev(str(category_output / "telehandlers.png"), "png", [], [])
            window.library.filter_to("All mods")
        window.search.set_text("courseplay")
        window._filter_changed()
        assert window.library.visible_names
        assert all("courseplay" in name.lower() for name in window.library.visible_names)
        window.search.set_text("")
        window._filter_changed()
        selected = set(window.current["mods"])
        target = next(n for n in hub.mods if n not in selected)
        window.library.cards[target].enabled.set_active(True)
        assert target in window.current["mods"]
        window.library.cards[target].favourite.set_active(True)
        assert target in hub.config["favorites"]
        assert window.library.cards[target].get_parent() == window.library.favourites_grid
        window.library.filter_to("Favourites")
        assert window.library.visible_names == {target}
        window.library.filter_to("Maps")
        assert all(hub.mods[n]["kind"] == "Maps" for n in window.library.visible_names)
        window.library.filter_to("All mods")
        window.library.sort_combo.set_active(2)
        pump()
        children = window.library.grid.get_children()
        assert hub.mods[children[0].mod_name]["size"] == max(m["size"] for n, m in hub.mods.items() if n != target)
        window.library.sort_combo.set_active(0)
        # Highlight two pictures and create a real collection in temporary app data.
        others = [n for n in hub.mods if n != target][:2]
        for name in others:
            window.library.grid.select_child(window.library.cards[name])
        assert set(window.library.highlighted_names()) == set(others)
        window.name_dialog = lambda *args, **kwargs: ("Interface check", None)
        window.create_collection()
        collection = hub.config["collections"][-1]
        assert set(collection["mods"]) == set(others)
        loadouts_before = copy.deepcopy(hub.config["loadouts"])
        window.library.grid.unselect_all()
        window.library.favourites_grid.select_child(window.library.cards[target])
        assert window.library.add_collection.get_sensitive()
        collection_response = Gtk.ResponseType.CANCEL
        collection_dialog_action = lambda dialog: None
        collection_screenshot = "add-to-collection"

        class CollectionDialog(Gtk.Dialog):
            def show_all(self):
                contents = self.get_child()
                self.remove(contents)
                self.render = Gtk.OffscreenWindow()
                self.render.set_size_request(700, 620)
                self.render.add(contents)
                self.render.show_all()
                pump()

            def run(self):
                collection_dialog_action(self)
                pump()
                output = Path("/tmp/farmsim-hub-interface")
                output.mkdir(exist_ok=True)
                self.render.get_pixbuf().savev(str(output / (collection_screenshot + ".png")), "png", [], [])
                return collection_response

            def destroy(self):
                self.render.destroy()
                super().destroy()

        # Cancel, then add a highlighted favourite through the actual chooser.
        with patch.object(Gtk, "Dialog", CollectionDialog):
            window.library.add_collection.clicked()
        assert set(collection["mods"]) == set(others)
        collection_response = Gtk.ResponseType.OK
        with patch.object(Gtk, "Dialog", CollectionDialog):
            window.library.add_collection.clicked()
        assert set(collection["mods"]) == set(others) | {target}
        assert hub.config["loadouts"] == loadouts_before
        window.library.filter_to("All mods", collection)
        assert window.library.remove_collection.get_visible()
        window.library.remove_collection.clicked()
        assert window.library.visible_names == set(others)
        assert target not in window.library.highlighted_names()
        assert not window.library.remove_collection.get_sensitive()

        # The checklist searches without losing changes hidden by another query.
        def edit_members(dialog):
            search = find_widgets(dialog.render, Gtk.SearchEntry)[0]
            view = find_widgets(dialog.render, Gtk.TreeView)[0]
            toggle = view.get_column(0).get_cells()[0]
            for name in [target, others[0]]:
                search.set_text(name)
                search.emit("search-changed")
                assert len(view.get_model()) == 1
                toggle.emit("toggled", "0")
            search.set_text("")
            search.emit("search-changed")

        collection_dialog_action = edit_members
        collection_screenshot = "edit-collection"
        collection_response = Gtk.ResponseType.CANCEL
        with patch.object(Gtk, "Dialog", CollectionDialog):
            find_buttons(window.pages["Collections"], "Edit mods")[-1].clicked()
        assert set(collection["mods"]) == set(others)
        collection_response = Gtk.ResponseType.OK
        with patch.object(Gtk, "Dialog", CollectionDialog):
            window.library.edit_collection.clicked()
        assert set(collection["mods"]) == {target, others[1]}
        assert window.library.visible_names == {target, others[1]}
        assert hub.config["loadouts"] == loadouts_before
        assert Hub(data_dir=hub.data).config["collections"][-1]["mods"] == collection["mods"]
        pump()
        offscreen.get_pixbuf().savev("/tmp/farmsim-hub-interface/collection-members.png", "png", [], [])
        # Both cancellation and the visible Delete button use disposable metadata.
        loadouts_before = copy.deepcopy(hub.config["loadouts"])
        delete = find_buttons(window.pages["Collections"], "Delete collection")[-1]
        window.confirm = lambda *_: False
        delete.clicked()
        assert collection in hub.config["collections"]
        window.confirm = lambda *_: True
        delete.clicked()
        assert collection not in hub.config["collections"]
        assert window.library.collection_filter is None
        assert hub.config["loadouts"] == loadouts_before
        assert not hub.config["collections"] and not window.collection_navigation.get_children()
        disposable = hub.new_loadout("Delete this loadout", others)
        window._render_loadouts()
        window._reload_combo()
        window.choose_loadout(disposable)
        delete = find_buttons(window.pages["Loadouts"], "Delete loadout")[-1]
        window.confirm = lambda *_: False
        delete.clicked()
        assert disposable in hub.loadouts()
        window.confirm = lambda *_: True
        delete.clicked()
        assert disposable not in hub.loadouts()
        assert window.current and window.current is not disposable
        assert window.loadout_combo.get_active_id() == window.current["id"]
        assert find_buttons(window.pages["Loadouts"], "Export ZIP")
        assert find_buttons(window.pages["Loadouts"], "DLC choices")
        assert find_buttons(window.pages["Mod updates"], "Check all mods")
        # Deleting the last loadout leaves a usable library and an empty-state prompt.
        for loadout in hub.loadouts():
            hub.delete_loadout(loadout)
        window.current = None
        window.loaded()
        assert window.current is None
        assert all(not card.enabled.get_sensitive() for card in window.library.cards.values())
        assert find_buttons(window.pages["Loadouts"], "New loadout")
        hub.config["loadouts"] = loadouts_before
        hub.save_config()
        window.loaded()
        # Render the actual DLC dialog content and save a checkbox choice without
        # opening a native desktop window or changing any real game data.
        dlcs = [d for d in hub.dlcs() if d["installed"] and d.get("fileHash")]
        if dlcs:
            dlc_loadout = hub.new_loadout("DLC interface check")
            chosen = dlcs[0]

            class OffscreenDialog(Gtk.Dialog):
                def show_all(self):
                    contents = self.get_child()
                    self.remove(contents)
                    self.render = Gtk.OffscreenWindow()
                    self.render.set_size_request(580, 620)
                    self.render.add(contents)
                    self.render.show_all()
                    pump()
                    output = Path("/tmp/farmsim-hub-interface")
                    output.mkdir(exist_ok=True)
                    self.render.get_pixbuf().savev(str(output / "dlc-choices.png"), "png", [], [])

                def run(self):
                    def choose(widget):
                        if isinstance(widget, Gtk.CheckButton):
                            widget.set_active(widget.get_tooltip_text() == chosen["name"])
                        if isinstance(widget, Gtk.Container):
                            for child in widget.get_children():
                                choose(child)
                    choose(self.render)
                    return Gtk.ResponseType.OK

                def destroy(self):
                    self.render.destroy()
                    super().destroy()

            with patch.object(Gtk, "Dialog", OffscreenDialog):
                window.edit_loadout_dlcs(dlc_loadout)
            assert hub.selected_dlcs(dlc_loadout) == {chosen["name"]}
            hub.delete_loadout(dlc_loadout)
            window._render_loadouts()
        window.library.grid.unselect_all()
        window.library.cards[target].favourite.set_active(False)
        window._update_selection(selected)
        # Exercise the real Search button and background import-to-library path.
        live_browse = Path("/tmp/farmsim-hub-category-live/results.json")
        if not live_browse.is_file():
            live_browse = Path("/tmp/farmsim-hub-browse-live/results.json")
        browse_result = json.loads(live_browse.read_text()) if live_browse.is_file() else {
            "query": "Map Objects Hider", "page": 0, "has_next": False,
            "items": [{"id": "310506", "title": "Map Objects Hider", "author": "Achimobil", "picture": ""}]}
        window.mod_browser.search.set_text(browse_result["query"])
        with patch.object(window.modhub, "search", return_value=browse_result) as search:
            window.mod_browser.search_button.clicked()
            deadline = time.monotonic() + 20
            while window.busy and time.monotonic() < deadline:
                pump()
            assert not window.busy
            search.assert_called_once()
        assert len(window.mod_browser.grid.get_children()) == len(browse_result["items"])
        assert window.stack.get_visible_child_name() == "Find mods"
        staged = Path(directory) / "UI-fixture.zip"
        make_mod(staged, title="UI download", version="2.0.0.0")
        entry = {"id": "123456", "filename": "FS25_UI_Download.zip", "title": "UI download", "version": "2.0.0.0", "download_url": "https://cdn26.giants-software.com/fixture.zip"}
        selection_before = window.current["mods"][:]
        def download_fixture(url, destination, progress=None):
            destination.write_bytes(staged.read_bytes())
        with patch.object(window.modhub, "detail", return_value=entry), patch.object(window.modhub, "_download", side_effect=download_fixture):
            window.download_modhub_mod(entry["id"])
            deadline = time.monotonic() + 20
            while window.busy and time.monotonic() < deadline:
                pump()
            assert not window.busy
        assert entry["filename"] in hub.mods
        assert window.library.visible_names == {entry["filename"]}
        assert window.current["mods"] == selection_before
        (hub.data / "imports" / entry["filename"]).unlink()
        hub.scan()
        window.loaded()
        window.library.filter_to("All mods")
        live_github = Path("/tmp/farmsim-hub-github-live.json")
        if live_github.is_file():
            window.modhub.cache = json.loads(live_github.read_text())
            window.update_filter = 1
            window._render_updates()
        # Reference comparison state: an Alma map favourite and real local mod cards.
        alma = next((n for n in hub.mods if "alma_missouri" in n.lower()), None)
        if alma:
            window.toggle_mod(alma, favourite=True)
        hub.config["collections"] = [
            {"name": "AutoDrive", "color": "#60a5fa", "mods": [n for n in hub.mods if "autodrive" in n.lower()][:5]},
            {"name": "John Deere", "color": "#70bb37", "mods": [n for n in hub.mods if "johndeere" in n.lower()][:9]},
            {"name": "Forestry", "color": "#f2ad32", "mods": [n for n in hub.mods if any(k in n.lower() for k in ["forest", "log", "wood", "yarder"])][:27]},
        ]
        window._render_collections()
        window._refresh_sidebar()
        output = Path("/tmp/farmsim-hub-interface")
        output.mkdir(exist_ok=True)
        for width, height in [(2048, 974), (1360, 850), (1180, 740)]:
            offscreen.set_size_request(width, height)
            offscreen.resize(width, height)
            window._responsive_sidebar(None, SimpleNamespace(width=width))
            window.go("Mod library")
            pump()
            pixbuf = offscreen.get_pixbuf()
            assert pixbuf.get_width() == width, (width, pixbuf.get_width())
            assert pixbuf.get_height() == height, (height, pixbuf.get_height())
            pixbuf.savev(str(output / f"mod-library-{width}.png"), "png", [], [])
        offscreen.set_size_request(1360, 850)
        offscreen.resize(1360, 850)
        window._responsive_sidebar(None, SimpleNamespace(width=1360))
        for name in window.page_names:
            window.go(name)
            pump()
            offscreen.get_pixbuf().savev(str(output / (name.lower().replace(" ", "-") + ".png")), "png", [], [])
        print(json.dumps({"ui_pages_rendered": len(window.page_names), "mods": len(hub.mods),
                          "mod_cards": len(window.library.cards), "saves": len(hub.saves),
                          "mod_pictures": len(window.pictures.images),
                          "search_filters_sort_favourites_loadouts_collections": "passed",
                          "delete_cancel_delete_last_loadout_export_dlc_update_controls": "passed",
                          "modhub_search_button_and_download_into_library": "passed",
                          "category_badges_search_and_telehandler_filter": "passed",
                          "collection_add_remove_search_edit_cancel_and_persistence": "passed",
                          "viewports": [2048, 1360, 1180], "renders": str(output)}, indent=2))
        offscreen.destroy()
        window.destroy()


if __name__ == "__main__":
    main()
