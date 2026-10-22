"""Build normalized, side-by-side native design QA evidence."""

import argparse
from pathlib import Path
import shutil
import gi
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("iteration", type=int)
    args = parser.parse_args()
    output = Path(__file__).resolve().parent.parent / "design"
    source = GdkPixbuf.Pixbuf.new_from_file(str(output / "reference.png"))
    # The supplied remote-desktop screenshot is 3839 px wide; compare at one
    # consistent 2048 px app viewport after excluding remote/window chrome.
    normalized_height = round(source.get_height() * 2048 / source.get_width())
    source = source.scale_simple(2048, normalized_height, GdkPixbuf.InterpType.BILINEAR)
    source = source.new_subpixbuf(0, 80, 2048, 974)
    source.savev(str(output / "reference-normalized.png"), "png", [], [])
    implementation_path = output / f"iteration-{args.iteration}.png"
    if args.iteration != 1:
        shutil.copy2("/tmp/farmsim-hub-interface/mod-library-2048.png", implementation_path)
    implementation = GdkPixbuf.Pixbuf.new_from_file(str(implementation_path))
    for name, left, right in [
        ("full", source, implementation),
        ("header", source.new_subpixbuf(260, 0, 1788, 310), implementation.new_subpixbuf(260, 0, 1788, 310)),
        ("cards", source.new_subpixbuf(280, 300, 700, 670), implementation.new_subpixbuf(280, 300, 700, 670)),
    ]:
        combined = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8,
                                       left.get_width() + right.get_width(), max(left.get_height(), right.get_height()))
        combined.fill(0x333333ff)
        left.copy_area(0, 0, left.get_width(), left.get_height(), combined, 0, 0)
        right.copy_area(0, 0, right.get_width(), right.get_height(), combined, left.get_width(), 0)
        combined.savev(str(output / f"{name}-comparison-{args.iteration}.png"), "png", [], [])
    print(f"Comparison {args.iteration}: source left, native implementation right")


if __name__ == "__main__":
    main()
