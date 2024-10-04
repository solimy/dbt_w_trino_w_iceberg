import sys

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


match sys.argv[1]:
    case "list":
        match len(sys.argv):
            case 2:
                print(catalog.list_namespaces())
            case 3:
                print(catalog.list_tables(sys.argv[2]))
    case "scan":
        assert len(sys.argv) == 3
        print(catalog.load_table(sys.argv[2]).scan().to_pandas())
