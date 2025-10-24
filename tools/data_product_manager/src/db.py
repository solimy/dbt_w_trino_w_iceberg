import os
import json
import logging
from typing import Dict, List

import pydantic

logging.getLogger("app.db").handlers = logging.getLogger().handlers
logger = logging.getLogger("app.db")

DEFAULT_DB_PATH = "./products_db.json"


class DataProductState(pydantic.BaseModel):
    name: str
    domain: str
    description: str
    admin_emails: List[str]

    code_repository: bool = False
    keycloak_group: bool = False
    keycloak_domain_group: bool = False
    keycloak_product_group: bool = False
    trino_dev_catalog: bool = False
    trino_prd_catalog: bool = False


class LocalDB(pydantic.BaseModel):
    path: str = DEFAULT_DB_PATH
    data_products: Dict[str, DataProductState] = {}

    def load(self):
        if not os.path.exists(self.path):
            self.data_products = {}
            return
        with open(self.path, "r") as f:
            self.data_products = json.load(f).get("data_products", {})
        logger.debug(f"Loaded database from {self.path} with {len(self.data_products)} data products.")

    def save(self):
        with open(self.path, "w") as f:
            json.dump({"data_products": self.dict().get("data_products", {})}, f, indent=4)
        logger.debug(f"Saved database to {self.path} with {len(self.data_products)} data products.")

    def insert(self, dp: DataProductState):
        if dp.name in self.data_products:
            raise KeyError(f"Data product {dp.name} already exists in database.")
        self.data_products[dp.name] = dp
        logger.debug(f"Inserted data product {dp.name} into database.")

    def update(self, dp: str, **kwargs):
        if dp not in self.data_products:
            raise KeyError(f"Data product {dp} not found in database.")
        for k, v in kwargs.items():
            setattr(self.data_products[dp], k, v)
        logger.debug(f"Updated data product {dp} with {kwargs}.")

    def flush(self):
        self.save()
        logger.debug(f"Flushed database to {self.path}.")
    
    def __enter__(self):
        self.load()
        return self
    
    def __exit__(self, exc_type, exc_value, traceback):
        self.save()
