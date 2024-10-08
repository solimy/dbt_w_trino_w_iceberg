docker run \
--rm \
--volume $PWD/:/local/ \
airbyte/source-file:latest \
read \
--config=/local/source.json \
--catalog=/local/catalog.json \
| \
docker run \
--rm \
-i \
--volume $PWD/:/local/ \
--network host \
airbyte/destination-iceberg:latest \
write \
--config /local/destination.json \
--catalog /local/catalog.json \