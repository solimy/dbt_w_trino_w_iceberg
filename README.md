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