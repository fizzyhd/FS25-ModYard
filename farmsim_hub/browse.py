"""Native picture cards for searching the official ModHub."""

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf, GLib, Gtk, Pango

from .library import action, column, row, text
from .categories import MODHUB_CATEGORIES


class ModBrowser(Gtk.Box):
    def __init__(self, owner):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        self.owner = owner
        self.result = None
        self.pack_start(text("Find FS25 mods", "title"), False, False, 0)
        description = text("Search the official ModHub, view mod details, and download ZIPs into your library.", "subtitle")
        description.set_line_wrap(True)
        self.pack_start(description, False, False, 0)
        controls = row(8)
        self.search = Gtk.SearchEntry(placeholder_text="Search tractors, maps, equipment…")
        self.search.set_hexpand(True)
        self.search.connect("activate", lambda *_: owner.search_modhub())
        controls.pack_start(self.search, True, True, 0)
        self.category_combo = Gtk.ComboBoxText()
        self.category_combo.append("", "All categories")
        for category in sorted(MODHUB_CATEGORIES):
            self.category_combo.append(MODHUB_CATEGORIES[category], category)
        self.category_combo.set_active(0)
        self.category_combo.set_size_request(180, -1)
        for cell in self.category_combo.get_cells():
            if isinstance(cell, Gtk.CellRendererText):
                cell.props.ellipsize = Pango.EllipsizeMode.END
                cell.props.max_width_chars = 20
        controls.pack_start(self.category_combo, False, False, 0)
        self.search_button = action("Search ModHub", owner.search_modhub, "system-search-symbolic", True)
        controls.pack_start(self.search_button, False, False, 0)
        controls.pack_start(action("Latest mods", lambda: owner.search_modhub(latest=True)), False, False, 0)
        self.pack_start(controls, False, False, 0)
        self.summary = text("Enter a search or choose Latest mods.", "muted")
        self.pack_start(self.summary, False, False, 0)
        self.grid = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, column_spacing=12, row_spacing=12)
        self.grid.set_min_children_per_line(1)
        self.grid.set_max_children_per_line(8)
        self.grid.set_valign(Gtk.Align.START)
        self.grid.set_halign(Gtk.Align.FILL)
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.add(self.grid)
        self.pack_start(scroller, True, True, 0)
        navigation = row(8)
        self.previous = action("Previous page", lambda: owner.search_modhub(page=self.result["page"] - 1))
        self.next = action("Next page", lambda: owner.search_modhub(page=self.result["page"] + 1))
        self.previous.set_sensitive(False)
        self.next.set_sensitive(False)
        navigation.pack_start(self.previous, False, False, 0)
        navigation.pack_start(self.next, False, False, 0)
        self.pack_start(navigation, False, False, 0)

    def render(self, result):
        self.result = result
        self.category_combo.set_active_id(result.get("category", ""))
        for child in self.grid.get_children():
            child.destroy()
        self.summary.set_text(f"{len(result['items'])} mods on page {result['page'] + 1}" +
                              (f" · {result['query']}" if result["query"] else " · latest releases"))
        self.previous.set_sensitive(result["page"] > 0)
        self.next.set_sensitive(result["has_next"])
        for item in result["items"]:
            card = column(8)
            card.set_border_width(12)
            card.set_size_request(186, -1)
            card.set_halign(Gtk.Align.START)
            card.get_style_context().add_class("card")
            artwork = None
            if item.get("picture"):
                try:
                    artwork = GdkPixbuf.Pixbuf.new_from_file_at_scale(item["picture"], 160, 160, True)
                except GLib.Error:
                    pass
            image = Gtk.Image.new_from_pixbuf(artwork) if artwork else Gtk.Image.new_from_icon_name("package-x-generic", Gtk.IconSize.DIALOG)
            image.set_size_request(160, 160)
            card.pack_start(image, False, False, 0)
            title = text(item["title"])
            title.set_max_width_chars(20)
            title.set_ellipsize(Pango.EllipsizeMode.END)
            title.set_tooltip_text(item["title"])
            card.pack_start(title, False, False, 0)
            author = text(item.get("author") or "ModHub creator", "muted")
            author.set_max_width_chars(20)
            author.set_ellipsize(Pango.EllipsizeMode.END)
            author.set_tooltip_text(item.get("author", ""))
            card.pack_start(author, False, False, 0)
            category = text(item.get("category") or "ModHub mod", "muted")
            category.set_max_width_chars(20)
            category.set_ellipsize(Pango.EllipsizeMode.END)
            category.set_tooltip_text(item.get("category", ""))
            card.pack_start(category, False, False, 0)
            card.pack_start(action("Details and download", lambda i=item: self.owner.show_modhub_mod(i)), False, False, 0)
            self.grid.add(card)
        self.show_all()
