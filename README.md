# Getting started

## Preriquisites
- Python 3.12
- docker-compose


## Start trino server
Run the following command to start the trino server
```bash
cd dbt_w_trino_w_iceberg/
env UID=${UID} GID=${GID} docker compose up
```

## Setup dbt
Run the following command to setup the dbt environment
```bash
cd dbt_w_trino_w_iceberg/dbt
python3.12 -m venv --prompt dbt-trino .venv
source dbt/.venv/bin/activate
pip install poetry
poetry install
```

## Run dbt
Run the following command to run dbt
```bash
cd dbt_w_trino_w_iceberg/dbt
dbt run
```

## Check results with cli.py
```bash
cd dbt_w_trino_w_iceberg/dbt
python cli.py list
python cli.py list dbt
python cli.py scan dbt.incremental_model
```

## Run airbyte integration
```bash
cd dbt_w_trino_w_iceberg/airbyte
sh ingest.sh
````

## Check airbyte ingestion worked and is available in trino
```bash
cd dbt_w_trino_w_iceberg/dbt
dbt show --inline 'select * from {{ source("airbyte", "airbyte_raw_data") }}'
```

# System overview
```mermaid
C4Context
    title DBT x Trino

    Person_Ext(user, "User")
    Rel(user, dbt, "dbt run")
    Rel(user, cli, "python cli.py ...")

    Boundary(dbt_dir, "dbt_w_trino_w_iceberg/dbt") {
        System(dbt, "DBT", "Runs the queries")
        System(cli, "CLI", "CLI to interact with iceberg")
        Rel(cli, minio, "Read data")
        Rel(cli, postgres, "Read metadata")

        System(dbt, "DBT", "execute query")
        Rel(dbt, trino, "Query")
    }


    Boundary(dockercompose, "Docker Compose", "Runs the services") {
        Boundary(compute, "Compute", "Runs the queries") {
            System(trino, "Trino", "Query engine")
        }

        Boundary(storage, "Storage", "Stores data and metadata") {
            SystemDb(postgres, "Postgres", "Stores Iceberg metadata")
            System(minio, "MinIO", "Stores Iceberg data files")
        }

        Rel(trino, postgres, "Stores metadata")
        Rel(trino, minio, "Stores data")
    }

    Boundary(airbyte_dir, "dbt_w_trino_w_iceberg/airbyte") {
        SystemDb(airbyte_data, "data.csv")
        System(airbyte_src, "source-csv")
        System(airbyte_dest, "destination-iceberg")

        Rel(airbyte_src, airbyte_data, "Read data")
        Rel(airbyte_dest, airbyte_src, "Read records")
        Rel(airbyte_dest, postgres, "Write metadata")
        Rel(airbyte_dest, minio, "Write data")
    }


    UpdateLayoutConfig($c4ShapeInRow="1", $c4BoundaryInRow="3", $c4BoundaryInRow="2")
```