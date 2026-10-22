import argparse
import fcntl
import json
import os
import sys

from . import __version__
from .core import Hub, HubError


def main():
    parser = argparse.ArgumentParser(description="FS25 ModYard (unofficial)")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--diagnose", action="store_true", help="Scan and print a read-only summary; does not change game files")
    args = parser.parse_args()
    try:
        hub = Hub()
        lock_file = (hub.data / "app.lock").open("w")
        try:
            fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("FS25 ModYard is already open.", file=sys.stderr)
            return 1
        if args.diagnose:
            hub.scan()
            print(json.dumps({"profile_detected": (hub.profile / "gameSettings.xml").is_file(),
                              "mods": len(hub.mods), "saves": len(hub.saves), "loadouts": len(hub.loadouts()),
                              "dlc_files": len(hub.dlcs()), "mods_needing_attention": [m["name"] for m in hub.mods.values() if m["error"]],
                              "duplicate_filenames": len(hub.duplicates)}, indent=2))
            lock_file.close()
            return 0
        from .app import Gtk, GLib, Window
        GLib.set_prgname("fs25-modyard")
        GLib.set_application_name("FS25 ModYard")
        if not Gtk.init_check()[0]:
            print("A Linux graphical desktop is required. Use --diagnose for a scan.", file=sys.stderr)
            return 1
        window = Window(hub)
        Gtk.main()
        lock_file.close()
        return 0
    except HubError as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
