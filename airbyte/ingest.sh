#!/bin/bash

read_data() {
    docker run \
    --rm \
    --volume $PWD/:/local/ \
    airbyte/source-file:0.5.13 \
    read \
    --config=/local/source.json \
    --catalog=/local/catalog.json
}

write_data() {
    docker run \
    --rm \
    -i \
    --volume $PWD/:/local/ \
    --network host \
    airbyte/destination-iceberg:0.2.2 \
    write \
    --config /local/destination.json \
    --catalog /local/catalog.json
}

read_data | write_data