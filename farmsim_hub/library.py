"""Responsive native card browser for the mod library."""

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Pango

from .core import APP_ID, steam_libraries
from .pictures import scaled


LIBRARY_CSS = b"""
.library-hero { background-color: #1c2315; background-size: cover; background-position: center; }
.hero-shade { background-color: rgba(5, 8, 4, 0.86); padding: 31px 26px; }
.hero-shade > flowboxchild { padding: 0; }
.hero-title { font-size: 28px; font-weight: 800; color: #ffffff; }
.hero-caption { font-size: 14px; color: #c5c8b8; }
.field-caption { font-size: 10px; font-weight: bold; color: #90988a; }
.stat-tile { border-radius: 12px; padding: 20px 18px; border: 1px solid rgba(255,255,255,0.035); }
.stat-value { font-size: 32px; font-weight: 800; color: #ffffff; }
.stat-caption { font-size: 10px; font-weight: bold; }
.stat-green { background: #1a3013; color: #75b833; }
.stat-yellow { background: #2b2b10; color: #e1cb47; }
.stat-blue { background: #102434; color: #69aedc; }
.stat-purple { background: #1c1b2e; color: #a898e7; }
.section-label { color: #8c9686; font-weight: bold; font-size: 11px; }
.card-grid { background: transparent; padding: 0; }
.mod-card { background: #191e16; border: 2px solid #2a3124; border-radius: 12px; padding: 0; }
.mod-card:hover { border-color: #61774b; background: #20291a; }
.mod-card.enabled { border-color: #35472b; }
.mod-card.favourited { border-color: #4d9e1c; }
.mod-card.missing { border-color: #a87c34; }
.mod-card:selected { background: #24371c; box-shadow: inset 0 0 0 1px #a5c78a; }
.mod-name { font-size: 13px; font-weight: bold; color: #e7e9e3; }
.mod-author, .mod-version { font-size: 11px; color: #98a18f; }
.mod-card checkbutton { padding: 0; min-height: 18px; }
.mod-card check { min-width: 11px; min-height: 11px; margin-right: 2px; }
.mod-kind { background: rgba(9,13,8,0.85); color: #c3c9b7; border-radius: 4px; padding: 3px 5px; font-size: 9px; font-weight: bold; margin: 6px; }
.mod-kind.map { background: #14283a; color: #8cbfef; }
.mod-kind.script { background: #282c25; color: #d5d8ce; }
.library-toolbar button, .library-toolbar entry { min-height: 18px; padding: 6px 9px; }
button.favourite-toggle { background: rgba(14,18,13,0.9); border: 0; border-radius: 0 8px 0 8px; padding: 7px; min-width: 16px; min-height: 16px; color: #c9d0c3; }
button.favourite-toggle:checked { background: #4b9d19; color: white; }
.library-actionbar { border-top: 1px solid #242c20; padding-top: 10px; }
.library-sidebar button { border: 0; background: transparent; border-radius: 8px; padding: 10px 12px; color: #a0aa97; }
.library-sidebar button:hover { background: #1a2514; color: #d7e4cd; }
.library-sidebar button.nav-active { background: #1c3511; color: #8ccf4a; font-weight: bold; }
.nav-caption { color: #87927d; font-size: 10px; font-weight: bold; padding: 12px 12px 6px; }
.nav-count { border-radius: 10px; background: #28491b; color: #c7edaa; font-size: 10px; font-weight: bold; padding: 2px 6px; }
.sidebar-brand { font-size: 16px; font-weight: bold; color: #eef2e8; }
"""

FILTER_KINDS = ["All mods", "In loadout", "Favourites", "Maps", "Scripts", "Equipment", "Needs attention"]


def row(spacing=10):
    return Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=spacing)


def column(spacing=6):
    return Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=spacing)


def text(value, style=None):
    widget = Gtk.Label(label=str(value), xalign=0)
    if style:
        widget.get_style_context().add_class(style)
    return widget


def icon(name, size=16):
    image = Gtk.Image.new_from_icon_name(name, Gtk.IconSize.MENU)
    image.set_pixel_size(size)
    return image


def action(title, callback, image=None, primary=False):
    result = Gtk.Button()
    contents = row(7)
    contents.set_halign(Gtk.Align.CENTER)
    if image:
        contents.pack_start(icon(image), False, False, 0)
    contents.pack_start(text(title), False, False, 0)
    result.add(contents)
    result.connect("clicked", lambda *_: callback())
    if primary:
        result.get_style_context().add_class("suggested-action")
    return result


class ModCard(Gtk.FlowBoxChild):
    def __init__(self, browser, name):
        super().__init__()
        self.browser, self.mod_name = browser, name
        self.set_halign(Gtk.Align.START)
        self.set_valign(Gtk.Align.START)
        self.set_size_request(156, 236)
        self.get_style_context().add_class("mod-card")
        owner = browser.owner
        mod = owner.hub.mods.get(name)
        title = mod["title"] if mod else name
        kind = mod["kind"] if mod else "Missing"
        categories = mod.get("categories", [kind]) if mod else ["Missing"]
        category_text = ", ".join(categories)
        self.set_tooltip_text(f"{title}\n{category_text}\n{name}\nDouble-click for details. Ctrl-click to highlight multiple cards.")
        contents = column(0)
        self.add(contents)
        picture = Gtk.Overlay()
        artwork = owner.pictures.images.get(name)
        image = Gtk.Image.new_from_pixbuf(scaled(artwork, 148) if artwork else scaled(owner.placeholder, 148))
        image.set_size_request(148, 148)
        picture.add(image)
        badge = text(categories[0] + (f" +{len(categories) - 1}" if len(categories) > 1 else ""), "mod-kind")
        badge.set_max_width_chars(14)
        badge.set_ellipsize(Pango.EllipsizeMode.END)
        badge.set_tooltip_text(category_text)
        self.category_badge = badge
        badge.set_halign(Gtk.Align.START)
        badge.set_valign(Gtk.Align.START)
        if kind in {"Maps", "Scripts"}:
            badge.get_style_context().add_class("map" if kind == "Maps" else "script")
        picture.add_overlay(badge)
        self.favourite = Gtk.ToggleButton()
        self.favourite.add(icon("starred-symbolic", 14))
        self.favourite.set_halign(Gtk.Align.END)
        self.favourite.set_valign(Gtk.Align.START)
        self.favourite.get_style_context().add_class("favourite-toggle")
        self.favourite.set_tooltip_text("Favourite this mod")
        self.favourite_signal = self.favourite.connect("toggled", lambda *_: owner.toggle_mod(name, favourite=True))
        picture.add_overlay(self.favourite)
        contents.pack_start(picture, False, False, 0)
        information = column(3)
        information.set_border_width(10)
        self.enabled = Gtk.CheckButton()
        self.enabled.set_tooltip_text("Include this mod in the selected loadout")
        name_label = text(title, "mod-name")
        name_label.set_max_width_chars(13)
        name_label.set_ellipsize(Pango.EllipsizeMode.END)
        self.enabled.add(name_label)
        self.enabled_signal = self.enabled.connect("toggled", lambda *_: owner.toggle_mod(name))
        information.pack_start(self.enabled, False, False, 0)
        author = text(mod["author"] if mod else "Not found in your library", "mod-author")
        author.set_max_width_chars(16)
        author.set_ellipsize(Pango.EllipsizeMode.END)
        information.pack_start(author, False, False, 0)
        information.pack_start(text("v" + mod["version"] if mod and mod["version"] else "—", "mod-version"), False, False, 0)
        contents.pack_start(information, False, False, 0)
        if mod is None:
            self.get_style_context().add_class("missing")

    def update(self, enabled, favourite):
        self.enabled.set_sensitive(self.browser.owner.current is not None)
        for control, signal, value in [(self.enabled, self.enabled_signal, enabled),
                                       (self.favourite, self.favourite_signal, favourite)]:
            control.handler_block(signal)
            control.set_active(value)
            control.handler_unblock(signal)
        context = self.get_style_context()
        (context.add_class if enabled else context.remove_class)("enabled")
        (context.add_class if favourite else context.remove_class)("favourited")
        self.favourite.set_tooltip_text("Remove from favourites" if favourite else "Add to favourites")


class LibraryBrowser(Gtk.Box):
    def __init__(self, owner):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.owner = owner
        self.cards = {}
        self.visible_names = set()
        self.collection_filter = None
        self.hero = Gtk.Box()
        self.hero.get_style_context().add_class("library-hero")
        self.hero_row = Gtk.FlowBox()
        self.hero_row.set_selection_mode(Gtk.SelectionMode.NONE)
        self.hero_row.set_min_children_per_line(1)
        self.hero_row.set_max_children_per_line(2)
        self.hero_row.set_column_spacing(20)
        self.hero_row.set_row_spacing(14)
        self.hero_row.get_style_context().add_class("hero-shade")
        self.hero_row.set_hexpand(True)
        title = column(5)
        title.pack_start(text("FS25 GAME CONFIGURATOR", "hero-title"), False, False, 0)
        self.subtitle = text("Your mods, ready for your next farm.", "hero-caption")
        title.pack_start(self.subtitle, False, False, 0)
        title.set_hexpand(True)
        self.hero_row.add(title)
        controls = row(12)
        controls.set_valign(Gtk.Align.CENTER)
        controls.set_halign(Gtk.Align.END)
        save = column(4)
        save.pack_start(text("SAVE / LOADOUT", "field-caption"), False, False, 0)
        self.loadout_combo = Gtk.ComboBoxText()
        self.loadout_combo.set_size_request(260, -1)
        self.combo_signal = self.loadout_combo.connect("changed", owner._loadout_changed)
        save.pack_start(self.loadout_combo, False, False, 0)
        controls.pack_start(save, True, True, 0)
        source = column(4)
        source.pack_start(text("MOD FOLDERS", "field-caption"), False, False, 0)
        source.pack_start(action("Mod library", lambda: owner.go("Preferences"), "folder-symbolic"), False, False, 0)
        controls.pack_start(source, False, False, 0)
        apply = action("Apply loadout", owner.apply)
        self.apply_button = apply
        apply.set_valign(Gtk.Align.END)
        controls.pack_start(apply, False, False, 0)
        launch = action("Launch FS25", lambda: owner.apply(True), "media-playback-start-symbolic", True)
        self.launch_button = launch
        launch.set_valign(Gtk.Align.END)
        controls.pack_start(launch, False, False, 0)
        self.hero_row.add(controls)
        self.hero.add(self.hero_row)
        self.pack_start(self.hero, False, False, 0)
        content = column(18)
        content.set_margin_top(17)
        content.set_margin_start(26)
        content.set_margin_end(26)
        content.set_margin_bottom(16)
        self.pack_start(content, True, True, 0)
        stats = row(12)
        stats.set_homogeneous(True)
        self.stats = {}
        for key, title_text, color, image in [
            ("installed", "INSTALLED", "green", "package-x-generic-symbolic"),
            ("favourites", "FAVOURITED", "yellow", "starred-symbolic"),
            ("maps", "MAPS", "blue", "mark-location-symbolic"),
            ("disk", "DISK USAGE", "purple", "document-save-symbolic")]:
            tile = row(12)
            tile.get_style_context().add_class("stat-tile")
            tile.get_style_context().add_class("stat-" + color)
            values = column(3)
            value = text("0", "stat-value")
            self.stats[key] = value
            values.pack_start(value, False, False, 0)
            values.pack_start(text(title_text, "stat-caption"), False, False, 0)
            tile.pack_start(values, True, True, 0)
            tile.pack_end(icon(image, 23), False, False, 0)
            stats.pack_start(tile, True, True, 0)
        content.pack_start(stats, False, False, 0)
        filters = row(9)
        filters.get_style_context().add_class("library-toolbar")
        filters.pack_start(text("SHOW:", "field-caption"), False, False, 0)
        self.filter_combo = Gtk.ComboBoxText()
        for kind in FILTER_KINDS:
            self.filter_combo.append_text(kind)
        self.filter_combo.set_active(0)
        self.filter_combo.set_size_request(220, -1)
        for cell in self.filter_combo.get_cells():
            if isinstance(cell, Gtk.CellRendererText):
                cell.props.ellipsize = Pango.EllipsizeMode.END
                cell.props.max_width_chars = 22
        filters.pack_start(self.filter_combo, False, False, 0)
        self.search = Gtk.SearchEntry(placeholder_text="Search mods, authors, categories…")
        self.search.set_size_request(220, -1)
        filters.pack_start(self.search, False, False, 0)
        self.sort_combo = Gtk.ComboBoxText()
        for name in ["A–Z", "Z–A", "Largest first", "Newest first"]:
            self.sort_combo.append_text(name)
        self.sort_combo.set_active(0)
        self.sort_combo.set_size_request(176, -1)
        filters.pack_start(self.sort_combo, False, False, 0)
        filters.pack_start(action("Rescan", owner.refresh, "view-refresh-symbolic"), False, False, 0)
        tools = Gtk.MenuButton()
        tools.add(icon("open-menu-symbolic"))
        tools.set_tooltip_text("Mod actions and selection tools")
        menu = Gtk.Menu()
        tools.set_popup(menu)
        self.count = text("", "muted")
        summary = Gtk.MenuItem()
        summary.add(self.count)
        summary.set_sensitive(False)
        menu.append(summary)
        for title_text, callback in [("Select shown", lambda: owner.select_shown(True)),
                                      ("Clear shown", lambda: owner.select_shown(False)),
                                      ("New loadout", owner.create_loadout),
                                      ("Export current loadout", owner.export_loadout),
                                      ("Check ModHub updates", lambda: owner.check_mod_updates(True)),
                                      ("Make collection", owner.create_collection),
                                      ("Add to collection…", owner.add_to_collection),
                                      ("Import ZIP mods", owner.import_files)]:
            item = Gtk.MenuItem(label=title_text)
            item.connect("activate", lambda *_, cb=callback: cb())
            menu.append(item)
        menu.show_all()
        filters.pack_start(tools, False, False, 0)
        self.tools = tools
        content.pack_start(filters, False, False, 0)
        membership = row(9)
        self.add_collection = action("Add to collection…", owner.add_to_collection, "list-add-symbolic")
        self.add_collection.set_tooltip_text("Add highlighted mod cards to an existing collection")
        membership.pack_start(self.add_collection, False, False, 0)
        self.edit_collection = action("Edit collection…", lambda: owner.edit_collection(self.collection_filter))
        membership.pack_start(self.edit_collection, False, False, 0)
        self.remove_collection = action("Remove from collection", owner.remove_from_collection, "list-remove-symbolic")
        self.remove_collection.set_tooltip_text("Remove highlighted cards from this collection; keep their mod files and loadouts")
        membership.pack_start(self.remove_collection, False, False, 0)
        self.membership_hint = text("Highlight cards to add them to a collection.", "muted")
        self.membership_hint.set_ellipsize(Pango.EllipsizeMode.END)
        membership.pack_start(self.membership_hint, True, True, 0)
        content.pack_start(membership, False, False, 0)
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.scroller = scroller
        sections = column(18)
        self.favourites_heading = text("FAVOURITES", "section-label")
        sections.pack_start(self.favourites_heading, False, False, 0)
        self.favourites_grid = self._grid()
        sections.pack_start(self.favourites_grid, False, False, 0)
        self.all_heading = text("ALL MODS", "section-label")
        sections.pack_start(self.all_heading, False, False, 4)
        self.grid = self._grid()
        sections.pack_start(self.grid, False, False, 0)
        self.empty = text("No mods match these filters.", "muted")
        sections.pack_start(self.empty, False, False, 10)
        scroller.add(sections)
        content.pack_start(scroller, True, True, 0)
        self.search.connect("search-changed", lambda *_: owner._filter_changed())
        self.filter_signal = self.filter_combo.connect("changed", lambda *_: owner._filter_changed())
        self.sort_combo.connect("changed", lambda *_: self.sort_cards())

    def _grid(self):
        result = Gtk.FlowBox()
        result.set_homogeneous(True)
        result.set_min_children_per_line(1)
        result.set_max_children_per_line(24)
        result.set_column_spacing(10)
        result.set_row_spacing(10)
        result.set_selection_mode(Gtk.SelectionMode.MULTIPLE)
        result.set_activate_on_single_click(False)
        result.get_style_context().add_class("card-grid")
        result.set_filter_func(lambda child: child.mod_name in self.visible_names)
        result.set_sort_func(self._compare)
        result.connect("child-activated", lambda _, child: self.owner.show_mod_details(child.mod_name))
        result.connect("selected-children-changed", lambda *_: self.owner._counts())
        return result

    def set_categories(self, categories):
        selected = self.filter_combo.get_active_text()
        choices = FILTER_KINDS + sorted(set(categories) - set(FILTER_KINDS), key=str.casefold)
        self.filter_combo.handler_block(self.filter_signal)
        self.filter_combo.remove_all()
        for choice in choices:
            self.filter_combo.append_text(choice)
        self.filter_combo.set_active(choices.index(selected) if selected in choices else 0)
        self.filter_combo.handler_unblock(self.filter_signal)

    def _compare(self, left, right):
        comparison = (self._sort_key(left.mod_name) > self._sort_key(right.mod_name)) - (self._sort_key(left.mod_name) < self._sort_key(right.mod_name))
        return -comparison if self.sort_combo.get_active() == 1 else comparison

    def _sort_key(self, name):
        mod = self.owner.hub.mods.get(name, {})
        title = mod.get("title", name).casefold()
        ordering = self.sort_combo.get_active()
        if ordering == 2:
            return -mod.get("size", 0), title, name
        if ordering == 3:
            fingerprint = mod.get("fingerprint") or [None, 0, 0]
            return -fingerprint[2], title, name
        return title, name

    def sort_cards(self):
        self.grid.invalidate_sort()
        self.favourites_grid.invalidate_sort()

    def rebuild(self):
        for grid in [self.grid, self.favourites_grid]:
            for child in grid.get_children():
                child.destroy()
        self.cards = {}
        favourites = set(self.owner.hub.config["favorites"])
        for entry in self.owner.model:
            name = entry[7]
            card = ModCard(self, name)
            card.update(entry[0], entry[1])
            self.cards[name] = card
            (self.favourites_grid if name in favourites else self.grid).add(card)
        self.show_all()
        self.update()

    def update(self):
        favourites = set(self.owner.hub.config["favorites"])
        for entry in self.owner.model:
            name = entry[7]
            card = self.cards.get(name)
            if card is None:
                continue
            card.update(entry[0], entry[1])
            parent = self.favourites_grid if name in favourites else self.grid
            if card.get_parent() != parent:
                card.get_parent().remove(card)
                parent.add(card)
                card.show_all()
        self.visible_names = {entry[7] for entry in self.owner.filtered}
        self.grid.invalidate_filter()
        self.favourites_grid.invalidate_filter()
        self.sort_cards()
        has_favourites = bool(self.visible_names & favourites)
        has_regular = bool(self.visible_names - favourites)
        for widget in [self.favourites_heading, self.favourites_grid]:
            widget.set_visible(has_favourites)
        self.grid.set_visible(has_regular)
        self.all_heading.set_visible(has_regular)
        self.empty.set_visible(not self.visible_names)
        self.all_heading.set_text((self.collection_filter["name"].upper() if self.collection_filter else "ALL MODS"))

    def highlighted_names(self):
        return [card.mod_name for grid in [self.favourites_grid, self.grid] for card in grid.get_selected_children()
                if card.mod_name in self.visible_names]

    def update_collection_controls(self):
        highlighted = bool(self.highlighted_names())
        self.add_collection.set_sensitive(highlighted and bool(self.owner.hub.config["collections"]))
        for widget in [self.edit_collection, self.remove_collection]:
            widget.set_visible(self.collection_filter is not None)
        self.remove_collection.set_sensitive(highlighted)
        self.membership_hint.set_text("Highlight cards to remove them from this collection." if self.collection_filter
                                      else "Highlight cards to add them to a collection.")

    def filter_to(self, kind, collection=None):
        self.collection_filter = collection
        self.search.set_text("")
        kinds = [entry[0] for entry in self.filter_combo.get_model()]
        self.filter_combo.set_active(kinds.index(kind))
        self.owner._filter_changed()
        self.owner.go("Mod library")

    def set_artwork(self, home):
        for library in steam_libraries(home):
            images = list((library / f"appcache/librarycache/{APP_ID}").glob("*/library_hero.jpg"))
            if images:
                provider = Gtk.CssProvider()
                provider.load_from_data(f'.library-hero {{ background-image: url("{images[0].as_uri()}"); }}'.encode())
                self.hero.get_style_context().add_provider(provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)
                break
