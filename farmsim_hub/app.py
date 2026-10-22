"""Native GTK3 interface. Background work never touches GTK widgets."""

import copy
import html
import re
import threading
from pathlib import Path

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gtk, Pango

from . import __version__
from .core import Hub, HubError, SETTING_FIELDS, detect_profiles, game_running
from .pictures import ModPictures, THUMBNAIL_SIZE
from .library import LibraryBrowser, LIBRARY_CSS, action, icon
from .modhub import BASE, ModHubClient
from .transfers import export_loadout
from .browse import ModBrowser
from .categories import matches_search


CSS = b"""
* { font-family: "Adwaita Sans", "Noto Sans", Sans; font-size: 13px; }
window, dialog { background: #0e120c; color: #e3e8dc; }
headerbar { background: #11160e; border-bottom: 1px solid #293123; box-shadow: none; }
.sidebar { background: #11170e; border-right: 1px solid #242c1e; }
.brand { font-size: 21px; font-weight: 800; color: #f1f7ef; }
.eyebrow { color: #9bcc75; font-size: 10px; font-weight: bold; }
.muted { color: #929d88; }
.title { font-size: 27px; font-weight: bold; }
.subtitle { font-size: 14px; color: #929d88; }
.card { background: #192015; border: 1px solid #2c3725; border-radius: 9px; padding: 14px; }
.detail { background: #172011; border: 1px solid #2c3725; border-radius: 7px; padding: 10px; }
.footer { background: #0d120a; border-top: 1px solid #242c1e; padding: 8px 16px; }
button { background-image: none; background: #1a2115; color: #e6eadf; border: 1px solid #333f29; border-radius: 7px; padding: 7px 12px; box-shadow: none; text-shadow: none; }
button:hover { background: #2a3820; }
button:disabled { opacity: 0.45; }
button.suggested-action { background: #4b9d19; color: #ffffff; border-color: #5aad25; font-weight: bold; }
button.suggested-action:hover { background: #60b52b; }
button.destructive-action { color: #f2a7a0; border-color: #73483f; }
button.destructive-action:hover { background: #532d26; }
entry, spinbutton, combobox button { background: #181e14; color: #e6eadf; border-color: #303a27; }
entry { padding: 6px 9px; border-radius: 6px; }
check:checked { background-color: #4b9d19; color: #ffffff; border-color: #77b34b; }
list { background: transparent; }
list row { padding: 0; }
list row:selected { background: #22362e; color: #c8e9b3; }
.navrow { padding: 13px 18px; }
scrolledwindow { border-color: #283320; }
separator { background: #283320; }
tooltip { background: #25341b; color: #ffffff; }
"""


def label(text, style=None, wrap=False):
    result = Gtk.Label(label=str(text), xalign=0)
    if style:
        result.get_style_context().add_class(style)
    if wrap:
        result.set_line_wrap(True)
        result.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
    return result


def button(text, callback, primary=False):
    result = Gtk.Button(label=text)
    result.connect("clicked", lambda *_: callback())
    if primary:
        result.get_style_context().add_class("suggested-action")
    return result


def box(vertical=True, spacing=10, margin=0):
    result = Gtk.Box(orientation=Gtk.Orientation.VERTICAL if vertical else Gtk.Orientation.HORIZONTAL, spacing=spacing)
    if margin:
        result.set_border_width(margin)
    return result


def scroll(child):
    result = Gtk.ScrolledWindow()
    result.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
    result.add(child)
    return result


def size_text(size):
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size < 1024 or unit == "TB":
            return f"{size:.1f} {unit}"
        size /= 1024


class Window(Gtk.Window):
    def __init__(self, hub=None, auto_scan=True, visible=True):
        super().__init__(title="FS25 ModYard")
        self.hub = hub or Hub()
        self.modhub = ModHubClient(self.hub.data)
        self.pictures = ModPictures(self.hub.data / "thumbnails")
        try:
            self.placeholder = Gtk.IconTheme.get_default().load_icon("image-x-generic", THUMBNAIL_SIZE, 0)
        except GLib.Error:
            self.placeholder = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8, THUMBNAIL_SIZE, THUMBNAIL_SIZE)
            self.placeholder.fill(0x2B3A40FF)
        self.busy = False
        self.current = None
        self.error = None
        self.set_default_size(1360, 850)
        self.set_size_request(900, 620)
        self.set_icon_name("fs25-modyard")
        icon_file = Path(__file__).resolve().parent.parent / "icon.svg"
        if icon_file.is_file():
            self.set_icon_from_file(str(icon_file))
        self.connect("delete-event", self._closing)
        provider = Gtk.CssProvider()
        provider.load_from_data(CSS + LIBRARY_CSS)
        Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        Gtk.Settings.get_default().set_property("gtk-application-prefer-dark-theme", True)

        outer = box(spacing=0)
        self.add(outer)
        body = box(False, 0)
        outer.pack_start(body, True, True, 0)
        self.page_names = ["Mod library", "Find mods", "Saves", "Collections", "Loadouts", "Mod updates", "Installed DLC", "Backups", "Preferences", "About"]
        self.sidebar = self._build_sidebar()
        body.pack_start(self.sidebar, False, False, 0)
        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        body.pack_start(self.stack, True, True, 0)
        self.pages = {}
        for name in self.page_names:
            page = box(margin=22, spacing=14)
            self.pages[name] = page
            self.stack.add_named(page, name)
        self._build_mods()
        self.mod_browser = ModBrowser(self)
        self.pages["Find mods"].pack_start(self.mod_browser, True, True, 0)
        self._build_preferences()
        self._build_about()
        self.go("Mod library")
        footer = box(spacing=6)
        footer.get_style_context().add_class("footer")
        self.status = label("Detecting your FS25 setup…", "muted")
        self.status.set_ellipsize(Pango.EllipsizeMode.END)
        self.status.set_hexpand(False)
        self.game_status = label("", "muted")
        self.spinner = Gtk.Spinner()
        status_row = box(False, 8)
        status_row.pack_start(self.spinner, False, False, 0)
        status_row.pack_start(self.game_status, False, False, 0)
        footer.pack_start(status_row, False, False, 0)
        self.status.set_width_chars(1)
        self.status.set_max_width_chars(24)
        footer.pack_start(self.status, False, False, 0)
        self.sidebar.pack_end(footer, False, False, 0)
        self.game_timer = GLib.timeout_add_seconds(3, self._game_status)
        self.connect("size-allocate", self._responsive_sidebar)
        self._game_status()
        if visible:
            self.show_all()
        if auto_scan:
            GLib.idle_add(self.refresh)

    def _closing(self, *_):
        if self.busy:
            self.notice("An operation is still running. Please wait for it to finish before closing.")
            return True
        Gtk.main_quit()
        return False

    def _game_status(self):
        self.game_status.set_text("● FS25 running" if game_running() else "● FS25 closed")
        return True

    def toast(self, text):
        self.status.set_text(text)
        self.status.set_tooltip_text(text)

    def notice(self, text, title="FS25 ModYard"):
        dialog = Gtk.MessageDialog(transient_for=self, modal=True, message_type=Gtk.MessageType.INFO,
                                   buttons=Gtk.ButtonsType.OK, text=title)
        dialog.format_secondary_text(str(text))
        dialog.run()
        dialog.destroy()

    def confirm(self, title, text):
        dialog = Gtk.MessageDialog(transient_for=self, modal=True, message_type=Gtk.MessageType.QUESTION,
                                   buttons=Gtk.ButtonsType.OK_CANCEL, text=title)
        dialog.format_secondary_text(text)
        dialog.set_default_response(Gtk.ResponseType.CANCEL)
        result = dialog.run() == Gtk.ResponseType.OK
        dialog.destroy()
        return result

    def _run(self, work, success=None, message="Working…"):
        if self.busy:
            return
        self.busy = True
        self.error = None
        self.stack.set_sensitive(False)
        self.sidebar.set_sensitive(False)
        self.spinner.start()
        self.toast(message)

        def progress(text):
            GLib.idle_add(self.toast, text)

        def done(result, error):
            self.busy = False
            self.stack.set_sensitive(True)
            self.sidebar.set_sensitive(True)
            self.spinner.stop()
            if error:
                self.error = str(error)
                self.toast(str(error).splitlines()[0])
                self.notice(error, "Could not complete the operation")
            elif success:
                success(result)
            return False

        def worker():
            try:
                result = work(progress)
            except Exception as exc:
                GLib.idle_add(done, None, exc)
            else:
                GLib.idle_add(done, result, None)

        threading.Thread(target=worker, daemon=True).start()

    def refresh(self):
        if self.busy:
            return False
        self._run(self._scan_library, lambda _: self.loaded(), "Scanning your mod library…")
        return False

    def _scan_library(self, progress):
        self.hub.scan(progress)
        self.pictures.prepare(self.hub.mods, progress)

    def loaded(self):
        loadouts = self.hub.loadouts()
        key = self.current["id"] if self.current else self.hub.config.get("selected_loadout")
        self.current = next((l for l in loadouts if l["id"] == key), None)
        if self.current is None and loadouts:
            most_recent = max(self.hub.saves, key=lambda s: s["mtime"], default=None)
            self.current = next((l for l in loadouts if most_recent and l.get("slot") == most_recent["slot"]), loadouts[0])
        self._reload_combo()
        self._populate_mods()
        self._render_saves()
        self._render_collections()
        self._render_loadouts()
        self._render_updates()
        self._render_dlcs()
        self._render_backups()
        self._refresh_preferences()
        self._refresh_sidebar()
        count = len(self.hub.mods)
        errors = sum(bool(m["error"]) for m in self.hub.mods.values())
        self.toast(f"{count} mods · {len(self.hub.saves)} saves detected" +
                   (f" · {errors} mods need attention" if errors else " · Ready"))
        if not (self.hub.profile / "gameSettings.xml").is_file():
            self.toast("Choose your FS25 user folder in Preferences to get started.")

    def go(self, name):
        if name == "Mod updates":
            self._render_updates()
        self.stack.set_visible_child_name(name)
        for control, page in self.nav_buttons:
            context = control.get_style_context()
            (context.add_class if page == name else context.remove_class)("nav-active")

    def _build_sidebar(self):
        sidebar = box(spacing=0)
        sidebar.set_size_request(220, -1)
        sidebar.set_hexpand(False)
        sidebar.get_style_context().add_class("sidebar")
        sidebar.get_style_context().add_class("library-sidebar")
        brand = box(False, 12, 18)
        logo_file = Path(__file__).resolve().parent.parent / "icon.svg"
        if logo_file.exists():
            brand.pack_start(Gtk.Image.new_from_pixbuf(GdkPixbuf.Pixbuf.new_from_file_at_scale(str(logo_file), 40, 40, True)), False, False, 0)
        names = box(spacing=3)
        names.pack_start(label("FS25 ModYard", "sidebar-brand"), False, False, 0)
        names.pack_start(label("FS25 Companion", "muted"), False, False, 0)
        brand.pack_start(names, True, True, 0)
        sidebar.pack_start(brand, False, False, 4)
        self.nav_buttons, self.nav_counts = [], {}
        navigation = box(spacing=3, margin=8)

        def navigation_button(title, image, callback, page=None, count=None):
            control = Gtk.Button()
            content = box(False, 10)
            content.pack_start(icon(image), False, False, 0)
            content.pack_start(label(title), True, True, 0)
            if count:
                value = label("0", "nav-count")
                self.nav_counts[count] = value
                content.pack_end(value, False, False, 0)
            control.add(content)
            control.connect("clicked", lambda *_: callback())
            if page:
                self.nav_buttons.append((control, page))
            return control

        navigation.pack_start(label("WORKSPACE", "nav-caption"), False, False, 0)
        for title, image, page in [("Mod Manager", "view-grid-symbolic", "Mod library"),
                                   ("Configure Save", "preferences-system-symbolic", "Saves"),
                                   ("Loadouts", "view-list-symbolic", "Loadouts"),
                                   ("Mod updates", "software-update-available-symbolic", "Mod updates"),
                                   ("Backups", "document-revert-symbolic", "Backups")]:
            navigation.pack_start(navigation_button(title, image, lambda p=page: self.go(p), page), False, False, 0)
        navigation.pack_start(label("LIBRARY", "nav-caption"), False, False, 0)
        navigation.pack_start(navigation_button("Find mods", "system-search-symbolic", lambda: self.go("Find mods"), "Find mods"), False, False, 0)
        for title, image, kind, count in [("Favourites", "starred-symbolic", "Favourites", "favourites"),
                                          ("Mods", "view-grid-symbolic", "All mods", "mods"),
                                          ("Maps", "mark-location-symbolic", "Maps", "maps"),
                                          ("Scripts", "applications-system-symbolic", "Scripts", "scripts")]:
            navigation.pack_start(navigation_button(title, image, lambda k=kind: self.library.filter_to(k), count=count), False, False, 0)
        navigation.pack_start(navigation_button("Installed DLC", "package-x-generic-symbolic", lambda: self.go("Installed DLC"), "Installed DLC"), False, False, 0)
        navigation.pack_start(label("COLLECTIONS", "nav-caption"), False, False, 0)
        self.collection_navigation = box(spacing=3)
        navigation.pack_start(self.collection_navigation, False, False, 0)
        navigation.pack_start(navigation_button("New collection", "list-add-symbolic", self.create_collection), False, False, 0)
        navigation.pack_start(navigation_button("Manage collections", "folder-symbolic", lambda: self.go("Collections"), "Collections"), False, False, 0)
        scroller = scroll(navigation)
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        sidebar.pack_start(scroller, True, True, 0)
        bottom = box(spacing=2, margin=8)
        for title, image, page in [("Settings", "preferences-system-symbolic", "Preferences"),
                                   ("About", "help-about-symbolic", "About")]:
            bottom.pack_start(navigation_button(title, image, lambda p=page: self.go(p), page), False, False, 0)
        bottom.pack_start(label(f"v{__version__} · Unofficial Linux app", "muted"), False, False, 8)
        sidebar.pack_end(bottom, False, False, 0)
        return sidebar

    def _responsive_sidebar(self, _, allocation):
        width = 260 if allocation.width >= 1600 else 220
        if self.sidebar.get_size_request().width != width:
            self.sidebar.set_size_request(width, -1)

    def _refresh_sidebar(self):
        values = {"mods": len(self.hub.mods), "favourites": len(set(self.hub.config["favorites"]) & set(self.hub.mods)),
                  "maps": sum(m["kind"] == "Maps" for m in self.hub.mods.values()),
                  "scripts": sum(m["kind"] == "Scripts" for m in self.hub.mods.values())}
        for key, widget in self.nav_counts.items():
            widget.set_text(str(values[key]))
        for child in self.collection_navigation.get_children():
            child.destroy()
        for collection in self.hub.config["collections"]:
            control = Gtk.Button()
            content = box(False, 9)
            marker = icon("media-record-symbolic", 11)
            color = Gdk.RGBA()
            color.parse(collection.get("color", "#75b833"))
            marker.override_color(Gtk.StateFlags.NORMAL, color)
            content.pack_start(marker, False, False, 0)
            name = label(collection["name"])
            name.set_ellipsize(Pango.EllipsizeMode.END)
            content.pack_start(name, True, True, 0)
            content.pack_end(label(str(len(collection["mods"])), "muted"), False, False, 0)
            control.add(content)
            control.connect("clicked", lambda *_, c=collection: self.library.filter_to("All mods", c))
            self.collection_navigation.pack_start(control, False, False, 0)
        self.collection_navigation.show_all()

    def heading(self, page, title, subtitle):
        page.pack_start(label(title, "title"), False, False, 0)
        page.pack_start(label(subtitle, "subtitle", True), False, False, 0)

    def _build_mods(self):
        page = self.pages["Mod library"]
        page.set_border_width(0)
        self.model = Gtk.ListStore(bool, bool, str, str, str, str, str, str, GdkPixbuf.Pixbuf)
        self.library = LibraryBrowser(self)
        self.search = self.library.search
        self.filter_combo = self.library.filter_combo
        self.loadout_combo = self.library.loadout_combo
        self.combo_signal = self.library.combo_signal
        self.count = self.library.count
        self.filtered = self.model.filter_new()
        self.filtered.set_visible_func(self._visible)
        self.library.set_artwork(self.hub.home)
        page.pack_start(self.library, True, True, 0)

    def _filter_changed(self):
        if hasattr(self, "filtered"):
            self.filtered.refilter()
            self.library.update()
            self._counts()


    def _reload_combo(self):
        self.loadout_combo.handler_block(self.combo_signal)
        self.loadout_combo.remove_all()
        for loadout in self.hub.loadouts():
            self.loadout_combo.append(loadout["id"], loadout["name"])
        if self.current:
            self.loadout_combo.set_active_id(self.current["id"])
        self.loadout_combo.set_sensitive(bool(self.hub.loadouts()))
        self.loadout_combo.handler_unblock(self.combo_signal)

    def _loadout_changed(self, combo):
        key = combo.get_active_id()
        if key:
            self.current = self.hub.loadout(key)
            self.hub.config["selected_loadout"] = key
            self.hub.save_config()
            self._populate_mods()
            self._render_dlcs()

    def choose_loadout(self, loadout):
        self.loadout_combo.set_active_id(loadout["id"])
        self.go("Mod library")

    def _populate_mods(self):
        self.library.set_categories(c for m in self.hub.mods.values() for c in m.get("categories", [m["kind"]]))
        self.model.clear()
        selected = set(self.current["mods"]) if self.current else set()
        favourites = set(self.hub.config["favorites"])
        names = set(self.hub.mods) | selected
        for name in sorted(names, key=lambda n: self.hub.mods.get(n, {}).get("title", n).casefold()):
            mod = self.hub.mods.get(name)
            if mod:
                self.model.append([name in selected, name in favourites, mod["title"], mod["kind"], mod["version"],
                                   mod["author"], size_text(mod["size"]), name, self.pictures.thumbnails.get(name, self.placeholder)])
            else:
                self.model.append([True, name in favourites, name + " (missing)", "Needs attention", "", "", "—", name, self.placeholder])
        self.library.rebuild()
        self._counts()


    def _visible(self, model, iterator, *_):
        row = model[iterator]
        collection = self.library.collection_filter
        if collection and row[7] not in collection["mods"]:
            return False
        query = self.search.get_text().casefold().strip()
        mod = self.hub.mods.get(row[7], {"title": row[2], "name": row[7], "author": row[5], "kind": row[3]})
        if query and not matches_search(mod, query):
            return False
        kind = self.filter_combo.get_active_text()
        return (kind == "All mods" or (kind == "In loadout" and row[0]) or
                (kind == "Favourites" and row[1]) or kind == row[3] or kind in mod.get("categories", []))


    def _counts(self):
        self.library.apply_button.set_sensitive(self.current is not None)
        self.library.launch_button.set_sensitive(self.current is not None)
        names = self.current["mods"] if self.current else []
        missing = sum(n not in self.hub.mods for n in names)
        highlighted = len(self.library.highlighted_names())
        self.library.update_collection_controls()
        self.count.set_text((f"{len(names)} selected" if self.current else "Create a loadout in Loadouts") + f" · {len(self.filtered)} shown" +
                            (f" · {missing} missing" if missing else "") +
                            (f" · {highlighted} highlighted" if highlighted else ""))
        favourites = len(set(self.hub.config["favorites"]) & set(self.hub.mods))
        maps = sum(m["kind"] == "Maps" for m in self.hub.mods.values())
        self.library.subtitle.set_text(f"{len(self.hub.mods)} mods installed · {favourites} favourited")
        for key, value in {"installed": len(self.hub.mods), "favourites": favourites, "maps": maps,
                           "disk": size_text(sum(m["size"] for m in self.hub.mods.values()))}.items():
            self.library.stats[key].set_text(str(value))
        for key, value in {"mods": len(self.hub.mods), "favourites": favourites, "maps": maps,
                           "scripts": sum(m["kind"] == "Scripts" for m in self.hub.mods.values())}.items():
            self.nav_counts[key].set_text(str(value))


    def _update_selection(self, names):
        missing = self.hub.set_selection(self.current, names)
        for row in self.model:
            row[0] = row[7] in self.current["mods"]
        self.filtered.refilter()
        self.library.update()
        self._counts()
        self.toast("Loadout saved" + (f" · {len(missing)} missing mods or dependencies" if missing else " · Apply it when you are ready to play"))


    def _toggle(self, _, path, column):
        self.toggle_mod(self.filtered[path][7], favourite=column == 1)

    def toggle_mod(self, name, favourite=False):
        if favourite:
            names = set(self.hub.config["favorites"])
            names.symmetric_difference_update({name})
            self.hub.config["favorites"] = sorted(names)
            self.hub.save_config()
            for entry in self.model:
                if entry[7] == name:
                    entry[1] = name in names
            self.filtered.refilter()
            self.library.update()
            self._counts()
            return
        if not self.current:
            return
        names = set(self.current["mods"])
        if name == self.hub.map_mod(self.current) and name in names:
            self.library.update()
            self.toast("The map mod stays selected for this save.")
            return
        names.symmetric_difference_update({name})
        self._update_selection(names)
        if name not in names and name in self.current["mods"]:
            self.toast("That mod is required by another selected mod and stays in this loadout.")


    def select_shown(self, enabled):
        if self.current:
            visible = {row[7] for row in self.filtered}
            names = set(self.current["mods"])
            self._update_selection(names | visible if enabled else names - visible)

    def show_mod_details(self, name):
        mod = self.hub.mods.get(name)
        if not mod:
            self.notice(f"{name} is referenced by this loadout but is missing from your mod folders.")
            return
        dialog = Gtk.Dialog(title=mod["title"], transient_for=self, modal=True)
        dialog.add_buttons("Close", Gtk.ResponseType.CLOSE)
        dialog.set_default_size(530, 300)
        content = box(False, 20, 22)
        if name in self.pictures.images:
            content.pack_start(Gtk.Image.new_from_pixbuf(self.pictures.images[name]), False, False, 0)
        info = box(spacing=9)
        info.pack_start(label(mod["title"], "title", True), False, False, 0)
        info.pack_start(label(f"{mod['author']} · v{mod['version']}", "muted", True), False, False, 0)
        info.pack_start(label(", ".join(mod.get("categories", [mod["kind"]])) + " · " + size_text(mod["size"]), wrap=True), False, False, 0)
        info.pack_start(label("Dependencies: " + (", ".join(mod["dependencies"]) or "None"), wrap=True), False, False, 0)
        filename = label(name, "muted", True)
        filename.set_selectable(True)
        info.pack_start(filename, False, False, 0)
        content.pack_start(info, True, True, 0)
        dialog.get_content_area().pack_start(content, True, True, 0)
        dialog.show_all()
        dialog.run()
        dialog.destroy()


    def name_dialog(self, title, initial="", with_slot=False):
        dialog = Gtk.Dialog(title=title, transient_for=self, modal=True)
        dialog.add_buttons("Cancel", Gtk.ResponseType.CANCEL, "Save", Gtk.ResponseType.OK)
        dialog.set_default_response(Gtk.ResponseType.OK)
        content = dialog.get_content_area()
        content.set_border_width(20)
        content.set_spacing(12)
        entry = Gtk.Entry(text=initial, activates_default=True)
        entry.set_width_chars(38)
        content.pack_start(label("Name"), False, False, 0)
        content.pack_start(entry, False, False, 0)
        combo = Gtk.ComboBoxText()
        if with_slot:
            combo.append("0", "Independent loadout · choose a save in-game")
            for save in self.hub.saves:
                combo.append(str(save["slot"]), f"Save {save['slot']} · {save['map']}")
            combo.set_active(0)
            content.pack_start(label("Link to a save"), False, False, 0)
            content.pack_start(combo, False, False, 0)
        dialog.show_all()
        response = dialog.run()
        value = entry.get_text().strip()
        slot = int(combo.get_active_id() or 0) or None
        dialog.destroy()
        return (value, slot) if response == Gtk.ResponseType.OK and value else (None, None)

    def create_loadout(self, clone=False):
        name, slot = self.name_dialog("Clone loadout" if clone else "New loadout", with_slot=True)
        if name:
            loadout = self.hub.new_loadout(name, self.current["mods"] if clone and self.current else [], slot)
            if clone and self.current and "dlcs" in self.current:
                loadout["dlcs"] = list(self.current["dlcs"])
            self.hub.set_selection(loadout, loadout["mods"])
            self.current = loadout
            self._reload_combo()
            self._populate_mods()
            self._render_loadouts()
            self._render_dlcs()

    def create_collection(self):
        names = self.library.highlighted_names()
        if not names:
            self.notice("Highlight mod cards first. Use Ctrl-click or Shift-click on the pictures to select several cards, then choose Make collection.")
            return
        name, _ = self.name_dialog(f"Collection from {len(names)} highlighted mods")
        if name:
            colors = ["#9bcc75", "#e5bd6b", "#80b8dd", "#c6a0df", "#e69c83"]
            self.hub.config["collections"].append({"name": name, "mods": names,
                                                   "color": colors[len(self.hub.config['collections']) % len(colors)]})
            self.hub.save_config()
            self._render_collections()
            self._refresh_sidebar()
            self._counts()
            self.toast(f"Created collection: {name}")

    def _save_collection_members(self, collection, names):
        try:
            self.hub.set_collection_mods(collection, names)
        except (HubError, OSError) as exc:
            self.notice(exc)
            return False
        self._render_collections()
        self._refresh_sidebar()
        self._filter_changed()
        self.toast(f"Saved collection: {collection['name']} · {len(collection['mods'])} mods")
        return True

    def add_to_collection(self):
        names = self.library.highlighted_names()
        if not names:
            self.notice("Highlight mod pictures first. Use Ctrl-click or Shift-click to select several cards.")
            return
        collections = list(self.hub.config["collections"])
        if not collections:
            self.notice("Create a collection first with New collection in the sidebar.")
            return
        dialog = Gtk.Dialog(title="Add to collection", transient_for=self, modal=True)
        dialog.add_buttons("Cancel", Gtk.ResponseType.CANCEL, "Add mods", Gtk.ResponseType.OK)
        dialog.set_default_response(Gtk.ResponseType.OK)
        content = dialog.get_content_area()
        content.set_border_width(20)
        content.set_spacing(12)
        content.pack_start(label(f"Add {len(names)} highlighted mods to:"), False, False, 0)
        combo = Gtk.ComboBoxText()
        for collection in collections:
            combo.append_text(f"{collection['name']} · {len(collection['mods'])} mods")
        active = next((i for i, c in enumerate(collections) if c is self.library.collection_filter), 0)
        combo.set_active(active)
        content.pack_start(combo, False, False, 0)
        dialog.show_all()
        response = dialog.run()
        index = combo.get_active()
        dialog.destroy()
        if response == Gtk.ResponseType.OK and index >= 0:
            collection = collections[index]
            self._save_collection_members(collection, set(collection["mods"]) | set(names))

    def remove_from_collection(self):
        collection = self.library.collection_filter
        names = self.library.highlighted_names()
        if collection and names:
            self._save_collection_members(collection, set(collection["mods"]) - set(names))

    def edit_collection(self, collection):
        if collection is None:
            return
        dialog = Gtk.Dialog(title=f"Edit collection: {collection['name']}", transient_for=self, modal=True)
        dialog.add_buttons("Cancel", Gtk.ResponseType.CANCEL, "Save collection", Gtk.ResponseType.OK)
        dialog.set_default_size(700, 620)
        content = dialog.get_content_area()
        content.set_border_width(18)
        content.set_spacing(12)
        content.pack_start(label("Check mods to add them; uncheck mods to remove them. Your loadouts and mod files are kept.", wrap=True), False, False, 0)
        search = Gtk.SearchEntry(placeholder_text="Search mods, authors, categories…")
        content.pack_start(search, False, False, 0)
        members = set(collection["mods"])
        choices = Gtk.ListStore(bool, str, str, GdkPixbuf.Pixbuf)
        for name in sorted(set(self.hub.mods) | members,
                           key=lambda n: (n not in members, self.hub.mods.get(n, {}).get("title", n).casefold(), n)):
            mod = self.hub.mods.get(name)
            choices.append([name in members, mod["title"] if mod else name + " (missing)", name,
                            self.pictures.thumbnails.get(name, self.placeholder)])
        filtered = choices.filter_new()
        filtered.set_visible_func(lambda model, row, _: not search.get_text().strip() or
                                  matches_search(self.hub.mods.get(model[row][2], {"name": model[row][2]}), search.get_text()))
        view = Gtk.TreeView(model=filtered)
        view.set_headers_visible(False)
        view.set_tooltip_column(2)
        toggle = Gtk.CellRendererToggle()
        def toggled(_, path):
            row = filtered.get_iter(path)
            child = filtered.convert_iter_to_child_iter(row)
            choices[child][0] = not choices[child][0]
            update_count()
        toggle.connect("toggled", toggled)
        view.append_column(Gtk.TreeViewColumn("In collection", toggle, active=0))
        view.append_column(Gtk.TreeViewColumn("Picture", Gtk.CellRendererPixbuf(), pixbuf=3))
        title = Gtk.CellRendererText(ellipsize=Pango.EllipsizeMode.END)
        column = Gtk.TreeViewColumn("Mod", title, text=1)
        column.set_expand(True)
        view.append_column(column)
        content.pack_start(scroll(view), True, True, 0)
        count = label("", "muted")
        def update_count():
            count.set_text(f"{sum(row[0] for row in choices)} mods in this collection")
        update_count()
        content.pack_start(count, False, False, 0)
        search.connect("search-changed", lambda *_: filtered.refilter())
        dialog.show_all()
        response = dialog.run()
        names = [row[2] for row in choices if row[0]]
        dialog.destroy()
        if response == Gtk.ResponseType.OK:
            self._save_collection_members(collection, names)


    def apply(self, launch=False):
        if not self.current:
            return
        try:
            if game_running():
                raise HubError("Close FS25 before applying or launching a loadout.")
            self.hub.validate_loadout(self.current)
        except HubError as exc:
            self.notice(exc)
            return
        save = self.hub.save_for(self.current)
        text = f"Use {len(self.current['mods'])} mods from “{self.current['name']}”.\n\n"
        if save:
            selected = {Path(n).stem for n in self.current["mods"]}
            removed = [m["title"] for m in save["mods"] if not m["modName"].startswith("pdlc_") and m["modName"] not in selected]
            text += f"This updates mod selections for Save {save['slot']} ({save['map']}). "
            if removed:
                text += f"{len(removed)} previously active mods will be deselected. Objects from removed mods may disappear when you next save in-game. "
            if "dlcs" in self.current:
                text += f"Use {len(self.hub.selected_dlcs(self.current))} selected DLCs. "
                deselected_dlcs = [m["title"] for m in save["mods"] if m["modName"].startswith("pdlc_")
                                   and m["modName"] not in self.hub.selected_dlcs(self.current)]
                if deselected_dlcs:
                    text += "Deselecting DLC can remove its objects when you next save in-game. "
            text += "A full save backup is created first.\n\n"
        else:
            text += "This sets the game's mod folder. Choose your save and enable mods in-game.\n\n"
            if "dlcs" in self.current:
                text += "Choose this loadout's DLCs in-game, or link it to a save under DLC choices to apply those selections.\n\n"
        text += "Your library and existing mod-switcher profiles stay in place."
        if not self.confirm("Apply and launch FS25?" if launch else "Apply this loadout?", text):
            return
        loadout = copy.deepcopy(self.current)

        def work(progress):
            backup = self.hub.apply(loadout, progress)
            if launch:
                self.hub.launch(loadout.get("slot"))
            return backup

        def finished(backup):
            self.toast("Launch sent to Steam. Backup created." if launch else "Loadout applied. Full backup created.")
            self._render_backups()

        self._run(work, finished, "Preparing your loadout…")

    def _replace_page(self, name, title, subtitle):
        page = self.pages[name]
        for child in page.get_children():
            child.destroy()
        self.heading(page, title, subtitle)
        content = box(spacing=12)
        page.pack_start(scroll(content), True, True, 0)
        return page, content

    def _render_saves(self):
        page, content = self._replace_page("Saves", "Your farms", "Manage each save's mods, settings, and backups.")
        for save in self.hub.saves:
            row = box(False, 14)
            row.get_style_context().add_class("card")
            info = box(spacing=5)
            info.pack_start(label(f"SAVE {save['slot']}  ·  {save['date']}", "eyebrow"), False, False, 0)
            info.pack_start(label(save["map"]), False, False, 0)
            info.pack_start(label(f"{save['name']} · {len(save['mods'])} saved mod/DLC entries", "muted"), False, False, 0)
            row.pack_start(info, True, True, 0)
            actions = box(False, 6)
            actions.pack_start(button("Mods", lambda s=save: self.open_save_loadout(s)), False, False, 0)
            actions.pack_start(button("Settings", lambda s=save: self.edit_settings(s)), False, False, 0)
            actions.pack_start(button("Backup", lambda s=save: self.make_backup(s)), False, False, 0)
            actions.pack_start(button("Clone", lambda s=save: self.clone_save(s)), False, False, 0)
            row.pack_end(actions, False, False, 0)
            content.pack_start(row, False, False, 0)
        if not self.hub.saves:
            content.pack_start(label("No saves found. Choose your FS25 folder in Preferences, or create your first farm in FS25.", "muted", True), False, False, 0)
        page.pack_end(label("Create a fresh world in FS25. Clone makes an independent copy of an existing farm in an empty slot.", "muted", True), False, False, 0)
        page.show_all()

    def open_save_loadout(self, save):
        loadout = next((l for l in self.hub.loadouts() if l.get("slot") == save["slot"]), None)
        if not loadout:
            by_stem = {Path(n).stem: n for n in self.hub.mods}
            mods = [by_stem.get(m["modName"], m["modName"] + ".zip") for m in save["mods"] if not m["modName"].startswith("pdlc_")]
            loadout = self.hub.new_loadout(f"{save['map']} · Save {save['slot']}", mods, save["slot"])
            self._reload_combo()
            self._render_loadouts()
        self.choose_loadout(loadout)

    def edit_settings(self, save):
        dialog = Gtk.Dialog(title=f"Save {save['slot']} · Settings", transient_for=self, modal=True)
        dialog.set_default_size(580, 620)
        dialog.add_buttons("Cancel", Gtk.ResponseType.CANCEL, "Save settings", Gtk.ResponseType.OK)
        grid = Gtk.Grid(row_spacing=12, column_spacing=24, margin=20)
        widgets = {}
        for key, title, kind, values in SETTING_FIELDS:
            if key not in save["settings"]:
                continue
            value = save["settings"][key]
            if kind == "choice" and value not in {v[0] for v in values}:
                continue
            if kind == "bool":
                widget = Gtk.Switch(active=value == "true", halign=Gtk.Align.END)
            elif kind == "choice":
                widget = Gtk.ComboBoxText()
                for ident, text in values:
                    widget.append(ident, text)
                widget.set_active_id(value)
            elif kind in {"int", "number"}:
                widget = Gtk.SpinButton.new_with_range(values[0], values[1], 1 if kind == "int" else 0.5)
                widget.set_digits(0 if kind == "int" else 1)
                widget.set_value(float(value))
            else:
                widget = Gtk.Entry(text=value, max_length=120)
            row = len(widgets)
            title_label = label(title, wrap=True)
            title_label.set_hexpand(True)
            grid.attach(title_label, 0, row, 1, 1)
            grid.attach(widget, 1, row, 1, 1)
            widgets[key] = (kind, widget)
        content = dialog.get_content_area()
        content.pack_start(label("A full save backup is created before any changes.", "detail"), False, False, 8)
        content.pack_start(scroll(grid), True, True, 0)
        dialog.show_all()
        response = dialog.run()
        changes = {}
        if response == Gtk.ResponseType.OK:
            for key, (kind, widget) in widgets.items():
                if kind == "bool":
                    value = "true" if widget.get_active() else "false"
                elif kind == "choice":
                    value = widget.get_active_id()
                elif kind in {"int", "number"}:
                    value = str(widget.get_value_as_int()) if kind == "int" else str(widget.get_value())
                    if float(value) == float(save["settings"][key]):
                        continue
                else:
                    value = widget.get_text()
                if value != save["settings"][key]:
                    changes[key] = value
        dialog.destroy()
        if changes:
            self._run(lambda _: self.hub.save_settings(save["slot"], changes),
                      lambda _: (self.loaded(), self.toast("Save settings updated. Full backup created.")), "Backing up and saving settings…")

    def make_backup(self, save):
        self._run(lambda _: self.hub.backup(save["slot"]),
                  lambda _: (self._render_backups(), self.toast(f"Save {save['slot']} backed up.")), "Backing up the complete save…")

    def clone_save(self, save):
        name, _ = self.name_dialog("Name the cloned farm", save["name"] + " (copy)")
        if not name:
            return
        empty = [n for n in range(1, 21) if not (self.hub.profile / f"savegame{n}").exists() or
                 (not (self.hub.profile / f"savegame{n}").is_symlink() and not any((self.hub.profile / f"savegame{n}").iterdir()))]
        if not empty:
            self.notice("All 20 save slots are occupied.")
            return
        slot = empty[0]
        if self.confirm(f"Clone to Save {slot}?", f"Copy {save['map']} and its current progress into an independent save named “{name}”."):
            self._run(lambda _: self.hub.clone_save(save["slot"], slot, name), lambda _: self.loaded(), "Copying your farm…")

    def _render_collections(self):
        page, content = self._replace_page("Collections", "Keep good mods together", "Use Edit mods to add or remove members of any collection.")
        if not self.hub.config["collections"]:
            content.pack_start(label("Your collections will appear here. Try grouping your favourite tractors, forestry equipment, or essential scripts.", "detail", True), False, False, 0)
        for collection in self.hub.config["collections"]:
            row = box(spacing=12)
            row.get_style_context().add_class("card")
            info = box(spacing=6)
            title = label("")
            title.set_markup(f"<span foreground='{html.escape(collection['color'])}'>●</span>  <b>{html.escape(collection['name'])}</b>")
            title.set_ellipsize(Pango.EllipsizeMode.END)
            title.set_max_width_chars(70)
            info.pack_start(title, False, False, 0)
            info.pack_start(label(f"{len(collection['mods'])} mods", "muted"), False, False, 0)
            info.set_tooltip_text("\n".join(collection["mods"]))
            row.pack_start(info, False, False, 0)
            controls = box(False, 8)
            controls.pack_start(button("Edit mods", lambda c=collection: self.edit_collection(c)), False, False, 0)
            controls.pack_start(button("Add to loadout", lambda c=collection: self.use_collection(c, True)), False, False, 0)
            controls.pack_start(button("Remove from loadout", lambda c=collection: self.use_collection(c, False)), False, False, 0)
            delete = button("Delete collection", lambda c=collection: self.delete_collection(c))
            delete.get_style_context().add_class("destructive-action")
            controls.pack_start(delete, False, False, 0)
            row.pack_start(controls, False, False, 0)
            content.pack_start(row, False, False, 0)
        page.pack_end(label("Collections apply to the loadout currently selected in the mod library.", "muted", True), False, False, 0)
        page.show_all()

    def delete_collection(self, collection):
        if not self.confirm(f"Delete collection “{collection['name']}”?", "The collection will be removed. Its mod files and all loadouts will be kept."):
            return
        try:
            if not self.hub.delete_collection(collection):
                return
        except (HubError, OSError) as exc:
            self.notice(exc)
            return
        if self.library.collection_filter is collection:
            self.library.collection_filter = None
            self._filter_changed()
        self._render_collections()
        self._refresh_sidebar()
        self._counts()
        self.toast(f"Deleted collection: {collection['name']}")

    def use_collection(self, collection, enabled):
        if self.current:
            names = set(self.current["mods"])
            members = set(collection["mods"])
            self._update_selection(names | members if enabled else names - members)
            self.go("Mod library")

    def _render_loadouts(self):
        page, content = self._replace_page("Loadouts", "A setup for every farm", "Choose mods and DLCs, export a server bundle, or manage your saved setups.")
        actions = box(False, 10)
        actions.pack_start(button("New loadout", self.create_loadout), False, False, 0)
        actions.pack_start(button("Clone current loadout", lambda: self.create_loadout(True)), False, False, 0)
        actions.pack_start(button("Import ZIP mods", self.import_files), False, False, 0)
        page.pack_end(actions, False, False, 0)
        if not self.hub.loadouts():
            content.pack_start(label("No loadouts yet. Choose New loadout, or open a save's Mods button to make one from that farm.", "detail", True), False, False, 0)
        for loadout in self.hub.loadouts():
            row = box(spacing=12)
            row.get_style_context().add_class("card")
            info = box(spacing=6)
            info.pack_start(label(loadout["name"], wrap=True), False, False, 0)
            slot = f"Save {loadout['slot']}" if loadout.get("slot") else "Independent loadout"
            info.pack_start(label(f"{len(loadout['mods'])} mods · {len(self.hub.selected_dlcs(loadout))} DLCs · {slot}", "muted"), False, False, 0)
            row.pack_start(info, False, False, 0)
            controls = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, column_spacing=6, row_spacing=6)
            controls.set_min_children_per_line(1)
            controls.set_max_children_per_line(5)
            for text, callback in [("Open loadout", lambda l=loadout: self.choose_loadout(l)),
                                   ("Rename", lambda l=loadout: self.rename_loadout(l)),
                                   ("DLC choices", lambda l=loadout: self.edit_loadout_dlcs(l)),
                                   ("Export ZIP", lambda l=loadout: self.export_loadout(l)),
                                   ("Delete loadout", lambda l=loadout: self.delete_loadout(l))]:
                control = button(text, callback)
                if text == "Delete loadout":
                    control.get_style_context().add_class("destructive-action")
                controls.add(control)
            row.pack_start(controls, False, False, 0)
            content.pack_start(row, False, False, 0)
        page.pack_end(label("Export ZIP bundles actual mod files and dependencies. Extract the bundle and upload the ZIPs in mods/ to your dedicated server. DLC requirements are listed in loadout.json.", "muted", True), False, False, 0)
        page.show_all()

    def delete_loadout(self, loadout):
        if not self.confirm(f"Delete loadout “{loadout['name']}”?", "This removes the saved setup from the hub. Your mod files, farm saves, and applied mod folders will be kept."):
            return
        try:
            if not self.hub.delete_loadout(loadout):
                return
        except (HubError, OSError) as exc:
            self.notice(exc)
            return
        if self.current is loadout:
            self.current = None
        self.loaded()
        self.toast(f"Deleted loadout: {loadout['name']}")

    def export_loadout(self, loadout=None):
        loadout = loadout or self.current
        if not loadout:
            self.notice("Create a loadout before exporting.")
            return
        dialog = Gtk.FileChooserDialog(title=f"Export {loadout['name']}", transient_for=self, action=Gtk.FileChooserAction.SAVE)
        dialog.add_buttons("Cancel", Gtk.ResponseType.CANCEL, "Export ZIP", Gtk.ResponseType.OK)
        dialog.set_do_overwrite_confirmation(True)
        dialog.set_current_name((re.sub(r"[^\w .-]+", "_", loadout["name"]).strip(" .")[:100] or "loadout") + "-mods.zip")
        downloads = self.hub.home / "Downloads"
        if downloads.is_dir():
            dialog.set_current_folder(str(downloads))
        response = dialog.run()
        destination = Path(dialog.get_filename()) if response == Gtk.ResponseType.OK and dialog.get_filename() else None
        dialog.destroy()
        if destination:
            if destination.suffix.lower() != ".zip":
                destination = destination.with_name(destination.name + ".zip")
                if destination.exists() and not self.confirm("Replace this export?", f"Replace the existing file {destination}?"):
                    return
            snapshot = copy.deepcopy(loadout)
            self._run(lambda progress: export_loadout(self.hub, snapshot, destination, progress), self._export_finished, "Preparing the server bundle…")

    def _export_finished(self, result):
        self.toast(f"Exported {result['mods']} mods · {size_text(result['size'])}")
        self.notice(f"{result['mods']} mod ZIPs exported to:\n{result['path']}\n\nExtract the bundle and upload the ZIPs in mods/ to your server. DLC selections are listed in loadout.json; install licensed DLC separately on the server.", "Loadout exported")

    def edit_loadout_dlcs(self, loadout):
        dialog = Gtk.Dialog(title=f"DLC choices · {loadout['name']}", transient_for=self, modal=True)
        dialog.set_default_size(580, 620)
        dialog.add_buttons("Cancel", Gtk.ResponseType.CANCEL, "Save choices", Gtk.ResponseType.OK)
        contents = dialog.get_content_area()
        contents.set_border_width(20)
        contents.set_spacing(12)
        contents.pack_start(label("Link to a save to apply DLC selections", wrap=True), False, False, 0)
        slots = Gtk.ComboBoxText()
        slots.append("0", "Independent · choose DLCs in-game")
        for save in self.hub.saves:
            slots.append(str(save["slot"]), f"Save {save['slot']} · {save['map']}")
        slots.set_active_id(str(loadout.get("slot") or 0))
        contents.pack_start(slots, False, False, 0)
        contents.pack_start(label("Choices are saved with this loadout and included in exports. Apply loadout updates the linked save. FS25 handles DLC ownership and activation.", "muted", True), False, False, 0)
        checks = {}
        selected = self.hub.selected_dlcs(loadout)
        choices = box(spacing=12)
        for dlc in self.hub.dlcs():
            control = Gtk.CheckButton(label=dlc["title"] + (" · not installed" if not dlc["installed"] else ""))
            control.set_active(dlc["name"] in selected)
            control.set_tooltip_text(dlc["name"])
            checks[dlc["name"]] = control
            choices.pack_start(control, False, False, 0)
        if not checks:
            choices.pack_start(label("No DLC files found. Install owned DLC through Steam, start FS25 once, and rescan.", "muted", True), False, False, 0)

        def protect_map(*_):
            save = next((s for s in self.hub.saves if str(s["slot"]) == slots.get_active_id()), None)
            required = save["map_id"].split(".", 1)[0] if save else ""
            for name, control in checks.items():
                control.set_sensitive(name != required)
                if name == required:
                    control.set_active(True)
        slots.connect("changed", protect_map)
        protect_map()
        contents.pack_start(scroll(choices), True, True, 0)
        dialog.show_all()
        while dialog.run() == Gtk.ResponseType.OK:
            try:
                names = [name for name, control in checks.items() if control.get_active()]
                slot = int(slots.get_active_id() or 0) or None
                self.hub.set_loadout_dlcs(loadout, names, slot)
            except (HubError, OSError) as exc:
                self.notice(exc)
                continue
            self._reload_combo()
            if self.current is loadout:
                self._populate_mods()
            self._render_loadouts()
            self._render_dlcs()
            self.toast(f"Saved DLC choices: {loadout['name']}")
            break
        dialog.destroy()

    def rename_loadout(self, loadout):
        name, _ = self.name_dialog("Rename loadout", loadout["name"])
        if name:
            loadout["name"] = name[:120]
            self.hub.save_config()
            self._reload_combo()
            self._render_loadouts()

    def _render_updates(self):
        results = self.modhub.results(self.hub.mods)
        updates = sum(r["status"] == "Update available" for r in results)
        matched = sum(bool(r.get("id") or r.get("repo")) and not r.get("error") for r in results)
        unmatched = sum(r["status"] == "No exact match" for r in results)
        page, content = self._replace_page("Mod updates", "ModHub and GitHub updates",
                                          f"{updates} updates available · {matched} matched · {unmatched} without an exact match · {len(self.hub.mods) - len(results)} unchecked")
        actions = box(False, 8)
        actions.pack_start(button("Check current loadout", lambda: self.check_mod_updates(True)), False, False, 0)
        actions.pack_start(button("Check all mods", self.check_mod_updates, True), False, False, 0)
        actions.pack_start(button("Open mod backups", lambda: self.open_folder(self.hub.data / "mod-backups")), False, False, 0)
        page.pack_end(actions, False, False, 0)
        if not results:
            content.pack_start(label("Check the official FS25 ModHub, then find GitHub releases for unmatched mods. AutoDrive and its companion mods are recognised automatically. The first check can take a few minutes.", "detail", True), False, False, 0)
        filters = box(False, 8)
        show = Gtk.ComboBoxText()
        for name in ["Updates available", "All checked mods", "No exact match"]:
            show.append_text(name)
        show.set_active(getattr(self, "update_filter", 0))
        search = Gtk.SearchEntry(placeholder_text="Search checked mods…")
        search.set_text(getattr(self, "update_query", ""))
        filters.pack_start(show, False, False, 0)
        filters.pack_start(search, True, True, 0)
        content.pack_start(filters, False, False, 0)
        empty = label("No results in this view. Choose All checked mods to see matched versions and checks needing attention.", "muted", True)
        content.pack_start(empty, False, False, 0)
        rows = []
        for result in results:
            row = box(spacing=10)
            row.get_style_context().add_class("card")
            row.pack_start(label(result["local_title"], wrap=True), False, False, 0)
            text = f"{result['status']} · installed {result['installed_version'] or 'unknown'}"
            if result.get("version"):
                text += f" · {'GitHub' if result.get('source') == 'github' else 'ModHub'} {result['version']}"
            row.pack_start(label(text, "muted", True), False, False, 0)
            if result.get("repo"):
                row.pack_start(label("GitHub source: " + result["repo"], "muted", True), False, False, 0)
            filename = label(result["name"], "muted")
            filename.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
            filename.set_max_width_chars(70)
            row.pack_start(filename, False, False, 0)
            if result.get("error"):
                row.pack_start(label(result["error"], "muted", True), False, False, 0)
            if result.get("github_error"):
                row.pack_start(label("GitHub: " + result["github_error"], "muted", True), False, False, 0)
            for candidate in result.get("candidates", []):
                row.pack_start(Gtk.LinkButton.new_with_label(candidate["url"], "Possible GitHub source: " + candidate["repo"]), False, False, 0)
            controls = box(False, 8)
            if result["can_update"]:
                controls.pack_start(button("Download and replace", lambda r=result: self.install_mod_update(r), True), False, False, 0)
            elif result["status"] == "Update available":
                row.pack_start(label("Unpacked mods can be downloaded from their source page and imported as ZIPs.", "muted", True), False, False, 0)
            if result.get("url"):
                controls.pack_start(Gtk.LinkButton.new_with_label(result["url"], "GitHub release" if result.get("source") == "github" else "Official ModHub page"), False, False, 0)
            else:
                from urllib.parse import urlencode
                url = BASE + "mods.php?" + urlencode({"title": "fs2025", "lang": "en", "searchMod": result["local_title"]})
                controls.pack_start(Gtk.LinkButton.new_with_label(url, "Find on ModHub"), False, False, 0)
            controls.pack_start(button("Link update source", lambda r=result: self.link_modhub_page(r["name"])), False, False, 0)
            row.pack_start(controls, False, False, 0)
            content.pack_start(row, False, False, 0)
            rows.append((row, result))

        def filter_rows(*_):
            self.update_filter = show.get_active()
            self.update_query = search.get_text()
            query = self.update_query.casefold().strip()
            visible = 0
            for row, result in rows:
                status = result["status"]
                allowed = (self.update_filter == 1 or (self.update_filter == 0 and status == "Update available")
                           or (self.update_filter == 2 and status == "No exact match"))
                allowed = allowed and query in (result["local_title"] + " " + result["name"]).casefold()
                row.set_visible(allowed)
                visible += int(allowed)
            empty.set_visible(not visible and bool(results))
        show.connect("changed", filter_rows)
        search.connect("search-changed", filter_rows)
        checked_at = self.modhub.cache.get("checked_at")
        if checked_at:
            content.pack_end(label("Last check: " + checked_at[:19].replace("T", " ") + " UTC", "muted"), False, False, 0)
        content.pack_end(label("Updates use official ModHub downloads or matching GitHub release ZIPs and keep a backup. GitHub checks verify the packaged filename, mod identity, and available checksum. Updating a shared file affects every loadout using it.", "muted", True), False, False, 0)
        page.show_all()
        filter_rows()

    def check_mod_updates(self, current_only=False):
        if current_only and not self.current:
            self.notice("Choose a loadout first.")
            return
        names = set(self.current["mods"]) if current_only else set(self.hub.mods)
        mods = [copy.deepcopy(self.hub.mods[n]) for n in sorted(names) if n in self.hub.mods]
        if not mods:
            self.notice("There are no installed mods in this selection to check.")
            return
        self._run(lambda progress: self.modhub.check_mods(mods, progress),
                  lambda _: self._updates_finished(), "Checking ModHub and GitHub releases…")

    def _updates_finished(self):
        self.go("Mod updates")
        self.toast("Update check complete. Review available updates and unmatched mods.")

    def link_modhub_page(self, name):
        dialog = Gtk.Dialog(title="Link mod update source", transient_for=self, modal=True)
        dialog.add_buttons("Cancel", Gtk.ResponseType.CANCEL, "Check page", Gtk.ResponseType.OK)
        content = dialog.get_content_area()
        content.set_border_width(20)
        content.set_spacing(12)
        content.pack_start(label(f"Paste the official ModHub page or GitHub repository URL for {name}.", wrap=True), False, False, 0)
        entry = Gtk.Entry(placeholder_text="https://github.com/owner/repository", width_chars=60)
        content.pack_start(entry, False, False, 0)
        dialog.show_all()
        response = dialog.run()
        url = entry.get_text().strip()
        dialog.destroy()
        if response == Gtk.ResponseType.OK and url:
            mod = copy.deepcopy(self.hub.mods[name])
            self._run(lambda _: self.modhub.link(mod, url), lambda _: self._updates_finished(), "Verifying the mod update source…")

    def install_mod_update(self, result):
        if game_running():
            self.notice("Close FS25 before replacing an installed mod.")
            return
        source = "GitHub · " + result["repo"] if result.get("source") == "github" else "official ModHub"
        if not self.confirm(f"Update {result['local_title']}?",
                            f"Replace version {result['installed_version']} with version {result['version']} from {source}.\n\n"
                            "The installed ZIP is backed up first. Every loadout using this file will use the new version, and any installed dependencies will be added to those loadouts."):
            return

        def work(progress):
            updated = self.modhub.install_update(self.hub, result["name"], progress)
            self._scan_library(progress)
            return updated

        def finished(updated):
            self.loaded()
            self.go("Mod updates")
            self.toast(f"Updated {updated['name']} to {updated['version']} · previous ZIP backed up")
        self._run(work, finished, "Downloading the mod update…")

    def search_modhub(self, page=None, latest=False):
        if self.busy:
            return
        if latest:
            self.mod_browser.search.set_text("")
            self.mod_browser.category_combo.set_active(0)
        query = self.mod_browser.result["query"] if page is not None and self.mod_browser.result else self.mod_browser.search.get_text()
        category = self.mod_browser.result.get("category", "") if page is not None and self.mod_browser.result else self.mod_browser.category_combo.get_active_id()
        self.go("Find mods")
        self._run(lambda progress: self.modhub.search(query, page or 0, progress, category=category),
                  self.mod_browser.render, "Searching the official FS25 ModHub…")

    def show_modhub_mod(self, item):
        def details(entry):
            dialog = Gtk.Dialog(title=entry["title"], transient_for=self, modal=True)
            dialog.set_default_size(560, 360)
            dialog.add_buttons("Close", Gtk.ResponseType.CLOSE)
            installed = self.hub.mods.get(entry["filename"])
            dialog.add_button("Check installed update" if installed else "Download to library", Gtk.ResponseType.OK)
            dialog.get_widget_for_response(Gtk.ResponseType.OK).get_style_context().add_class("suggested-action")
            content = box(spacing=12, margin=20)
            content.pack_start(label(entry["title"], "title", True), False, False, 0)
            content.pack_start(label(f"By {entry['author']} · Version {entry['version']} · {entry['size']}", "muted", True), False, False, 0)
            content.pack_start(label(entry["filename"], "muted", True), False, False, 0)
            content.pack_start(label("Category: " + item.get("category", entry.get("category", "ModHub mod")), "muted", True), False, False, 0)
            if item.get("picture"):
                try:
                    pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(item["picture"], 160, 160, True)
                    content.pack_start(Gtk.Image.new_from_pixbuf(pixbuf), False, False, 0)
                except GLib.Error:
                    pass
            content.pack_start(Gtk.LinkButton.new_with_label(entry["url"], "Official ModHub page and description"), False, False, 0)
            if installed:
                content.pack_start(label(f"Installed version: {installed['version']}", "muted"), False, False, 0)
            content.pack_start(label("Downloads are added to your library. Include them in a loadout when you are ready.", "muted", True), False, False, 0)
            dialog.get_content_area().pack_start(content, True, True, 0)
            dialog.show_all()
            response = dialog.run()
            dialog.destroy()
            if response == Gtk.ResponseType.OK:
                if installed:
                    self._run(lambda progress: self.modhub.check_mods([copy.deepcopy(installed)], progress), lambda _: self._updates_finished(), "Checking the installed mod…")
                else:
                    self.download_modhub_mod(entry["id"])
        self._run(lambda _: self.modhub.detail(item["id"]), details, "Loading ModHub details…")

    def download_modhub_mod(self, mod_id):
        def work(progress):
            result = self.modhub.download_mod(self.hub, mod_id, progress)
            self._scan_library(progress)
            return result

        def finished(result):
            self.loaded()
            self.toast(f"Downloaded {result['title']} v{result['version']} · available in Mod Manager")
            self.go("Mod library")
            self.library.filter_to("All mods")
            self.search.set_text(result["name"])
            self._filter_changed()
            missing = self.hub.dependency_closure([result["name"]])[1]
            if missing:
                self.notice("This mod also needs:\n" + "\n".join(sorted(missing)) + "\n\nUse Find mods to download its dependencies.", "Mod downloaded")
        self._run(work, finished, "Downloading the ModHub ZIP into your library…")

    def _render_dlcs(self):
        dlcs = self.hub.dlcs()
        installed = sum(d["installed"] for d in dlcs)
        selected = self.hub.selected_dlcs(self.current) if self.current else set()
        page, content = self._replace_page("Installed DLC", "DLC choices for your loadout",
                                          f"{installed} installed · {len(selected)} selected" + (f" · {self.current['name']}" if self.current else " · create a loadout to choose DLCs"))
        if self.current:
            page.pack_end(button("Edit DLC choices and linked save", lambda: self.edit_loadout_dlcs(self.current), True), False, False, 0)
        for dlc in dlcs:
            row = box(False)
            row.get_style_context().add_class("card")
            row.pack_start(label(dlc["title"]), True, True, 0)
            detail = "Included" if dlc["name"] in selected else "Not in this loadout"
            detail += " · " + (size_text(dlc["size"]) if dlc["installed"] else "Not installed")
            row.pack_end(label(detail, "muted"), False, False, 0)
            content.pack_start(row, False, False, 0)
        page.pack_end(label("Choose DLCs from Loadouts → DLC choices. Applying a loadout writes the choices to its linked save and backs up the farm first. Independent loadouts keep a DLC checklist for exports; choose their DLCs in-game or link them to a save. Steam and FS25 manage ownership and activation.", "muted", True), False, False, 0)
        page.show_all()

    def _render_backups(self):
        page, content = self._replace_page("Backups", "A way back to your farm", "Full save backups are created before applying loadouts or changing save settings.")
        page.pack_end(button("Open mod update backups", lambda: self.open_folder(self.hub.data / "mod-backups")), False, False, 0)
        backups = self.hub.backups()
        if not backups:
            content.pack_start(label("No backups yet. You can make one from Saves, or let the app create one when you apply a change.", "detail", True), False, False, 0)
        for backup in backups:
            row = box(False, 12)
            row.get_style_context().add_class("card")
            info = box(spacing=5)
            info.pack_start(label(backup["map"] + (f" · Save {backup['slot']}" if backup.get("slot") else "")), False, False, 0)
            info.pack_start(label(f"{backup['created'][:19].replace('T', ' ')} UTC · {size_text(backup['size'])}", "muted"), False, False, 0)
            info.pack_start(label(backup["reason"], "muted", True), False, False, 0)
            row.pack_start(info, True, True, 0)
            row.pack_end(button("Restore", lambda b=backup: self.restore(b)), False, False, 0)
            content.pack_start(row, False, False, 0)
        page.pack_end(button("Open backup folder", lambda: self.open_folder(self.hub.data / "backups")), False, False, 0)
        page.show_all()

    def restore(self, backup):
        if self.confirm("Restore this backup?", "This restores the save and game mod-folder setting from this backup. Your current state is backed up first. Close FS25 before continuing."):
            self._run(lambda _: self.hub.restore(Path(backup["path"])),
                      lambda _: (self.loaded(), self.toast("Backup restored. Your previous state was backed up.")), "Restoring your backup…")

    def _build_preferences(self):
        page = self.pages["Preferences"]
        self.heading(page, "Your Linux setup", "Point the hub at FS25 and the folders where you keep mods.")
        self.profile_label = label("", "detail", True)
        self.profile_label.set_selectable(True)
        page.pack_start(label("FS25 USER FOLDER", "eyebrow"), False, False, 0)
        page.pack_start(self.profile_label, False, False, 0)
        row = box(False)
        row.pack_start(button("Choose folder", self.choose_profile), False, False, 0)
        row.pack_start(button("Auto-detect", self.auto_detect), False, False, 0)
        row.pack_start(button("Open folder", lambda: self.open_folder(self.hub.profile)), False, False, 0)
        page.pack_start(row, False, False, 0)
        page.pack_start(label("MOD SOURCES", "eyebrow"), False, False, 6)
        self.sources_label = label("", "detail", True)
        self.sources_label.set_selectable(True)
        page.pack_start(scroll(self.sources_label), True, True, 0)
        page.pack_start(button("Add a mod folder", self.add_source), False, False, 0)
        self.diagnostics = label("", "muted", True)
        page.pack_start(self.diagnostics, False, False, 0)

    def _refresh_preferences(self):
        self.profile_label.set_text(str(self.hub.profile) if (self.hub.profile / "gameSettings.xml").exists() else "No FS25 user folder detected")
        self.sources_label.set_text("\n\n".join(map(str, self.hub.mod_directories())) or "No mod sources found")
        text = f"App data and imports: {self.hub.data}"
        if self.hub.duplicates:
            text += f"\n{len(self.hub.duplicates)} duplicate filenames found; the first source is used."
        if self.hub.scan_errors:
            text += "\n" + "\n".join(self.hub.scan_errors[:3])
        self.diagnostics.set_text(text)

    def folder_dialog(self, title):
        dialog = Gtk.FileChooserDialog(title=title, transient_for=self, action=Gtk.FileChooserAction.SELECT_FOLDER)
        dialog.add_buttons("Cancel", Gtk.ResponseType.CANCEL, "Choose", Gtk.ResponseType.OK)
        response = dialog.run()
        path = Path(dialog.get_filename()) if response == Gtk.ResponseType.OK else None
        dialog.destroy()
        return path

    def choose_profile(self):
        path = self.folder_dialog("Choose the FarmingSimulator2025 user folder")
        if path:
            try:
                self.hub.set_profile(path)
                self.current = None
                self.refresh()
            except HubError as exc:
                self.notice(exc)

    def auto_detect(self):
        profiles = detect_profiles(self.hub.home)
        if profiles:
            self.hub.set_profile(profiles[0])
            self.current = None
            self.refresh()
        else:
            self.notice("No Steam/Proton FS25 user folder was found. Use Choose folder to select it manually.")

    def add_source(self):
        path = self.folder_dialog("Choose a folder containing ZIP mods")
        if path:
            value = str(path.resolve())
            if value not in self.hub.config["mod_dirs"]:
                self.hub.config["mod_dirs"].append(value)
                self.hub.save_config()
            self.refresh()

    def import_files(self):
        dialog = Gtk.FileChooserDialog(title="Import FS25 ZIP mods", transient_for=self, action=Gtk.FileChooserAction.OPEN)
        dialog.add_buttons("Cancel", Gtk.ResponseType.CANCEL, "Import", Gtk.ResponseType.OK)
        dialog.set_select_multiple(True)
        file_filter = Gtk.FileFilter()
        file_filter.set_name("ZIP mods")
        file_filter.add_pattern("*.zip")
        file_filter.add_pattern("*.ZIP")
        dialog.add_filter(file_filter)
        response = dialog.run()
        files = [Path(f) for f in dialog.get_filenames()] if response == Gtk.ResponseType.OK else []
        dialog.destroy()
        if files:
            def work(progress):
                result = self.hub.import_mods(files, progress)
                self._scan_library(progress)
                return result

            def finished(result):
                self.loaded()
                imported, skipped = result
                self.toast(f"Imported {len(imported)} mods · {len(skipped)} skipped")
                if skipped:
                    self.notice("\n".join(skipped[:12]), "Import results")

            self._run(work, finished, "Importing mods…")

    def open_folder(self, path):
        try:
            path.mkdir(parents=True, exist_ok=True)
            Gio.AppInfo.launch_default_for_uri(path.resolve().as_uri(), None)
        except Exception as exc:
            self.notice(exc)

    def _build_about(self):
        page = self.pages["About"]
        self.heading(page, "FS25 ModYard", "A native companion for Farming Simulator 25 on Linux.")
        page.pack_start(label(f"Version {__version__}\n\nManage mods, per-save loadouts, DLC choices, collections, server exports, and backups. Find and download mods from the official ModHub, and check updates from ModHub or GitHub releases. Launch through Steam + Proton. Your library and setups are stored locally; searches and downloads contact their source sites when requested.", "detail", True), False, False, 0)
        page.pack_start(label("This is an independent, unofficial application inspired by FarmSim Hub by The FarmSim Guy. It is not an official port, and it is not affiliated with or endorsed by The FarmSim Guy or GIANTS Software. It uses its own code and interface.", "muted", True), False, False, 0)
        link = Gtk.LinkButton.new_with_label("https://thefarmsimguy.com/game-creator", "The original FarmSim Hub by The FarmSim Guy")
        link.set_halign(Gtk.Align.START)
        page.pack_start(link, False, False, 0)
        page.pack_start(label("New worlds are created in FS25. Cloning copies an existing farm. DLC choices apply to linked saves; ownership and activation remain managed by FS25. Server exports contain mod ZIPs and a DLC checklist. The Steam launch request is supported; actual game loading depends on your Steam and Proton configuration.", "muted", True), False, False, 0)
