#!/usr/bin/env bash
set -euo pipefail

source_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
version="$(sed -n 's/^__version__ = "\([^"]*\)"$/\1/p' "$source_dir/farmsim_hub/__init__.py")"
if [[ -z "$version" ]]; then
    echo "Could not read the app version." >&2
    exit 1
fi

output_dir="${1:-$HOME/Downloads}"
mkdir -p -- "$output_dir"
archive="$output_dir/fs25-modyard-${version}.tar.gz"
checksum="${archive}.sha256"
tar -czf "$archive" \
    --exclude='.git' --exclude='design' --exclude='tests' --exclude='dist' \
    --exclude='__pycache__' --exclude='*.pyc' --exclude='design-qa.md' \
    --exclude='verification-*.md' \
    --transform="s,^$(basename -- "$source_dir"),fs25-modyard-${version}," \
    -C "$(dirname -- "$source_dir")" "$(basename -- "$source_dir")"
sha256sum "$archive" > "$checksum"
printf 'Bundle: %s\nSHA-256: %s\n' "$archive" "$checksum"
