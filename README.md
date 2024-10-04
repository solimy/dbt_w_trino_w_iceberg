# Getting started

## Preriquisites
- Python 3.12
- docker-compose


## Start trino server
Run the following command to start the trino server
```bash
env UID=${UID} GID=${GID} docker compose up
```

## Setup dbt
Run the following command to setup the dbt environment
```bash
cd dbt
python3.12 -m venv --prompt dbt-trino .venv
source dbt/.venv/bin/activate
pip install poetry
poetry install
```

## Run dbt
Run the following command to run dbt
```bash
dbt run
```

## Check results with cli.py
```bash
python cli.py list
python cli.py list dbt
python cli.py scan dbt.incremental_model
```

# System overview
```mermaid
C4Context
    title DBT x Trino

    Person_Ext(user, "User")
    Rel(user, dbt, "dbt run")
    Rel(user, cli, "python cli.py ...")

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

    System(cli, "CLI", "CLI to interact with iceberg")
    Rel(cli, minio, "Read data")
    Rel(cli, postgres, "Read metadata")

    System(dbt, "DBT", "execute query")
    Rel(dbt, trino, "Query")

    UpdateLayoutConfig($c4ShapeInRow="2", $c4BoundaryInRow="2")
```