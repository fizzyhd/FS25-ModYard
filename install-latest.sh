#!/usr/bin/env bash
set -euo pipefail

repo="fizzyhd/FS25-ModYard"
api="https://api.github.com/repos/${repo}/releases/latest"

for command_name in curl python3 tar sha256sum; do
    if ! command -v "$command_name" >/dev/null 2>&1; then
        echo "FS25 ModYard installer needs $command_name. Install it, then run this command again." >&2
        exit 1
    fi
done

tmpdir="$(mktemp -d "${TMPDIR:-/tmp}/fs25-modyard-install.XXXXXX")"
trap 'rm -rf -- "$tmpdir"' EXIT

curl -fLsS --retry 2 "$api" -o "$tmpdir/release.json"
version="$(python3 - "$tmpdir/release.json" <<'PY'
import json
import re
import sys

with open(sys.argv[1], encoding="utf-8") as stream:
    release = json.load(stream)

tag = release.get("tag_name", "")
match = re.fullmatch(r"v(\d+\.\d+\.\d+)", tag)
if release.get("draft") or release.get("prerelease") or not match:
    raise SystemExit("GitHub did not return a stable ModYard release.")

version = match.group(1)
assets = {asset.get("name") for asset in release.get("assets", [])}
archive = f"fs25-modyard-{version}.tar.gz"
if archive not in assets or f"{archive}.sha256" not in assets:
    raise SystemExit(f"Release {tag} is still preparing its download. Try again in a minute.")
print(version)
PY
)"

archive="fs25-modyard-${version}.tar.gz"
base="https://github.com/${repo}/releases/download/v${version}"
curl -fLsS --retry 2 "$base/$archive" -o "$tmpdir/$archive"
curl -fLsS --retry 2 "$base/$archive.sha256" -o "$tmpdir/$archive.sha256"
(cd "$tmpdir" && sha256sum -c "$archive.sha256")

tar -xzf "$tmpdir/$archive" -C "$tmpdir"
source_dir="$tmpdir/fs25-modyard-${version}"
if [[ ! -x "$source_dir/install.sh" ]]; then
    echo "The release archive is missing its installer. No files were installed." >&2
    exit 1
fi

distro_id=""
if [[ -r /etc/os-release ]]; then
    . /etc/os-release
    distro_id="${ID:-}"
fi

if [[ "$distro_id" == "steamos" ]]; then
    echo "SteamOS detected. Installing ModYard in your home folder without changing system packages."
    "$source_dir/install.sh" --no-deps
else
    "$source_dir/install.sh" --install-deps
fi
