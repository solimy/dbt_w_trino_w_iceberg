from pyiceberg.catalog.sql import SqlCatalog
from pyiceberg.catalog import load_catalog

catalog = SqlCatalog(
   "test",
   **{
       "uri": f"postgresql://admin:admin@localhost:5432/metastore_db",
       "warehouse":"s3://data",
       "s3.endpoint": "http://localhost:9000",
       "s3.access-key-id": "minioadmin",
       "s3.secret-access-key": "minioadmin",
   },
)

print(catalog.load_table("dbt.my_first_dbt_model").scan().to_pandas())
