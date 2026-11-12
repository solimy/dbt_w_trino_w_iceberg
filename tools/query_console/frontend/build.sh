#!/bin/sh
set -eu

frontend_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
output_dir="$(pwd)/dist"
work_dir=$(mktemp -d)
trap 'rm -rf "$work_dir"' EXIT

# Official Trino Query UI 3.0.0. Its lockfile supplies the build dependencies.
revision=606c3136a9982987e827148519ef78b83f78d3d3
checksum=a0f651b592dd86077d0489a8f9e984e4d315111a0d81e4e076aa5d0fb2dd6b97
archive="$work_dir/source.tar.gz"
source_dir="$work_dir/source"
curl --fail --silent --show-error --location --retry 3 \
    "https://codeload.github.com/trinodb/trino-query-ui/tar.gz/$revision" \
    --output "$archive"
actual=$(sha256sum "$archive")
if [ "${actual%% *}" != "$checksum" ]; then
    echo 'Unexpected checksum for the Trino Query UI archive.' >&2
    exit 1
fi

mkdir -p "$source_dir/console"
tar -xzf "$archive" -C "$source_dir" --strip-components=1
node "$frontend_dir/prepare-upstream.mjs" "$source_dir"
cp "$frontend_dir/index.html" "$frontend_dir/main.tsx" "$frontend_dir/session.ts" \
    "$frontend_dir/styles.css" "$frontend_dir/vite.config.mjs" "$source_dir/console/"

cd "$source_dir"
npm ci --no-audit --no-fund
npm exec -- vite build console --config console/vite.config.mjs --outDir "$output_dir" --emptyOutDir
cp LICENSE "$output_dir/TRINO-QUERY-UI-LICENSE.txt"
