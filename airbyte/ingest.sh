#!/bin/bash

airbyte_dir=$(cd "$(dirname "$0")" && pwd)

read_data() {
    docker run \
    --rm \
    --volume "$airbyte_dir:/local:ro" \
    airbyte/source-file:0.5.13 \
    read \
    --config=/local/source.json \
    --catalog=/local/catalog.json
}

write_data() (
    cd "$airbyte_dir/.." || exit
    env UID="$(id -u)" GID="$(id -g)" docker compose run --rm -T airbyte-destination
)

read_data #| write_data
