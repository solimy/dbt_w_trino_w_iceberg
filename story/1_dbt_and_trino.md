# Motivations

We (almost) all heard about DBT. At Artefact (where I work as a data engineer), it is a very common framework we use for our data transformations for several reasons:
1. It is a SQL-based framework, which makes it easy to learn and use for data analysts and data scientists.
2. It is a powerful framework that allows us to build complex data pipelines.
3. It is a framework that is easy to maintain and to scale with the warehouses we use.

Usually, we use DBT with a data warehouse like BigQuery, Snowflake, or DataBricks. But what if I want to use DBT locally on my laptop? I surely won't have a BigQuery or Snowflake instance running on my laptop. To run DBT locally, I could use a local database like DuckDB. But I don't just want to run DBT locally, I also want to use some fancy new data format like Iceberg. DuckDB does not support Iceberg. So what can I do?

I previously heard about Trino (formerly PrestoSQL) and after a quick search, I found out that Trino supports Iceberg. So I decided to give it a try and see if I could use DBT with Trino and Iceberg.

# Disclaimers

This story is not a tutorial on how to use DBT with Trino and Iceberg in production. It is just a story about how I tried to use DBT with Trino and Iceberg on my laptop.

Many choices I made in this story are not the best choices for a production setup. For example, I used Postgres as the metadata store for Iceberg, which is not the best choice for a production setup when using Trino and Iceberg.

# Concetps

- Trino : it is a query engine. It executes SQL queries on data it reads thanks to the configuration defined in its catalogs.
- Trino Catalog : it is a configuration that tells Trino how to read and write data. It provides the connector to use and the configuration of the connector.
- Trino Connector : it is the connector that Trino uses to read and write data. It is the implementation of the Trino connector API.
- Iceberg : it is a table format, combination of a metadata store and data files.
- Iceberg Catalog : it is where the metadata of the Iceberg tables is stored.
- MinIO Bucket : 
- MinIO S3 Uri

# What is Trino?

Trino is a distributed SQL query engine for big data. It is designed for high performance and can query large datasets in a matter of seconds. It is an open-source project that was created by Facebook and is now maintained by the Trino Software Foundation.

## How does Trino work (simplified)?

Trino is a distributed SQL query engine. It is composed of two main components: the Trino coordinator and the Trino workers. The Trino coordinator is responsible for parsing the SQL queries, planning the execution, and coordinating the execution of the query across the Trino workers. The Trino workers are responsible for executing the query and returning the results to the coordinator.

When a query is submitted to Trino, the coordinator parses the query and creates a query plan. The query plan is a directed acyclic graph (DAG) that represents the steps required to execute the query. The coordinator then sends the query plan to the Trino workers, which execute the query in parallel. The results are returned to the coordinator, which aggregates the results and returns them to the user.

Doesn't it sound a lot like how any modern datawarehouse works, but without the storage layer? Yes it does.

# What is Iceberg?

Iceberg is a table format that is designed for large-scale data processing. It is an open-source project that was created by Netflix and is now maintained by the Apache Software Foundation. Iceberg is designed to be fast, scalable, and reliable, and it is optimized for cloud storage systems like Amazon S3, Google Cloud Storage, and Azure Blob Storage.

## How does Iceberg work (simplified)?

Iceberg is split into two main components: the Iceberg metadata store and the Iceberg data files. The Iceberg metadata store is a database that stores the metadata for the Iceberg tables, such as the schema, partitioning, and file locations. The Iceberg data files are the actual data files that contain the data for the Iceberg tables, stored as Parquet or Avro.

When a query is submitted to Iceberg, the Iceberg metadata store is queried to retrieve the metadata for the table. The metadata is used to determine which data files need to be read to satisfy the query. The data files are then read in parallel, and the results are returned to the user.

# Setting up Trino and Iceberg

As stated in the simplified explanation of Trino and Iceberg, Trino is a query engine and Iceberg is a table format. In order to work, both need a storage layer to store and retrieve the data. So that we can use Iceberg, we also need a metadata store to store the metadata of the tables.

For this setup, I chose MinIO as the storage layer and Postgres as the metadata store.

To make deployment easy, I chose to use docker-compose to deploy the services.

## What is MinIO?

MinIO is an open-source object storage server that is compatible with Amazon S3. It is designed for high performance and can store large amounts of data in a distributed manner. MinIO is optimized for cloud storage systems like Amazon S3, Google Cloud Storage, and Azure Blob Storage.

It is also very easy to set up and use locally with Docker.

## Setting up MinIO with Docker Compose

To set up MinIO with Docker Compose, we need to define a service in the `docker-compose.yml` file that specifies the MinIO image, the ports to expose, the volumes to mount, and the healthcheck to perform.

First, because we will be mounting a volume to store the data, we need to create a directory to store the data. We will create a directory called `minio_volume` in the root directory of the project.


```bash
mkdir minio_volume

## clean up command
# rm -rf $(find .. -path '../minio_volume/*')
```

Once its done, we can define the MinIO service in the `docker-compose.yml` file in the root directory of the project.

```yaml
# docker-compose.yml
services:

  #postgres:...

  minio:
    image: quay.io/minio/minio:RELEASE.2024-10-13T13-34-11Z
    command: server /data --console-address ":9001"
    ports:
      - "9000:9000"
      - "9001:9001"
    volumes:
      - ${PWD}/minio_volume/:/data/
    healthcheck:
        test: ["CMD", "curl", "-f", "http://localhost:9001"]
        interval: 2s
        timeout: 2s
        retries: 5

  minio_init:
      image: minio/mc:RELEASE.2024-10-08T09-37-26Z
      depends_on:
        minio:
          condition: service_healthy
      entrypoint: >
        /bin/sh -c "
        /usr/bin/mc config host add myminio http://minio:9000 minioadmin minioadmin;
        /usr/bin/mc mb myminio/warehouse;
        exit 0;
        "

  #trino:...
```

> **`Remark`** : the `minio_init` service is used to create a bucket in MinIO. The bucket is called `warehouse`. If we didn't create the bucket, we would have to create it manually in the MinIO console, because Trino needs a bucket to store the Iceberg tables.

## The Iceberg metadata store : Postgres

Postgres is a powerful, open-source relational database that is designed for high performance and scalability. It is optimized for large-scale data processing and can store and retrieve large amounts of data in a distributed manner.

In order to work with Iceberg, we need a metadata store to store the metadata of the tables. Although Postgres is not the best choice for a metadata store for Iceberg, it is a good choice for this setup because it is easy to set up and use locally with Docker.

Because I am using Postgres as the metadata store for Iceberg, and it is not designed specifically for Iceberg, I will have to set up the Iceberg tables manually in Postgres.

>Note 1 : that Iceberg also supports other metadata stores, some which are designed specifically for Iceberg, implementing the Iceberg metadata store API (eg: Nessy, Polaris).

>Note 2 : the Iceberg catalog is not to be confused with the concept of a data catalog. The Iceberg catalog is a metadata store that stores the metadata of the Iceberg tables, while a data catalog is a tool that helps users discover and understand the data in their data lake or data warehouse.

## The Trino cluster

Although Trino should be deployed in a cluster to take advantage of its distributed nature, I chose to deploy a single-node Trino cluster for this setup because it is easier to set up and use locally with Docker.

### The Iceberg connector

Trino at its core a simply a query engine. To be able to read and write data, it needs a connector. The Iceberg connector is the connector that Trino uses to read and write data from Iceberg tables.

# Using DBT with Trino and Iceberg

# Did we just reinvent the data warehouse?

# Conclusion : lakehouse is the new data warehouse
