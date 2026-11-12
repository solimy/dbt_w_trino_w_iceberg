import os
import logging
from typing import List, Annotated

import fastapi
import pydantic

from src.db import LocalDB, DataProductState
from src.componants.code_repository import CodeRepository

DEFAULT_REPO_URL = "/Users/hadrien.daures/code/me/dbt_w_trino_w_iceberg/data_products"

logging.getLogger("app.api").handlers = logging.getLogger().handlers
logger = logging.getLogger("app.api")


app = fastapi.FastAPI()


@app.middleware("http")
async def wrap_exceptions(request: fastapi.Request, call_next):
    try:
        response = await call_next(request)
        return response
    except fastapi.HTTPException as exc:
        logger.error(f"HTTPException: {exc.detail}")
        raise exc
    except ValueError as exc:
        logger.error(f"ValueError: {str(exc)}")
        raise fastapi.HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.error(f"Unhandled Exception: {str(exc)}")
        raise fastapi.HTTPException(status_code=500, detail="Internal Server Error") from exc


class DataProductCreateData(pydantic.BaseModel):
    domain: Annotated[str, pydantic.Field(
        pattern="^[a-z0-9_]+$",
        description="Data domain name, e.g., marketing, sales, finance",
    )]
    name: Annotated[str, pydantic.Field(
        pattern="^[a-z0-9_]+$",
        description="Data product name, e.g., customer_360, sales_reporting",
    )]
    description: Annotated[str, pydantic.Field(
        min_length=10,
        max_length=200,
        description="Description of the data product",
    )]
    admin_emails: Annotated[List[
        Annotated[str, pydantic.Field(
            pattern="^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\\.[a-zA-Z]{2,}$",
            description="Admin email address",
        )]
    ], pydantic.Field(
        min_length=1,
        description="List of admin email addresses for the data product",
    )]


@app.post("/data-product")
def _create(data: DataProductCreateData):
    """Create a new data product."""
    params = {
        "command": "create",
        "domain": data.domain,
        "name": data.name,
        "description": data.description,
        "admin_emails": data.admin_emails,
    }
    logger.info(f"Creating data product {data.name}.")
    logger.debug(params)

    with LocalDB() as db, CodeRepository(url=os.environ.get("CODE_REPOSITORY_URL", DEFAULT_REPO_URL)) as code_repo:
        if data.name in db.data_products:
            detail={
                "error": f"data product already exists",
                "data_product": data.name,
            }
            logger.error(detail)
            raise fastapi.HTTPException(status_code=400, detail=detail)

        logger.debug(f"Using code repository at {code_repo.url} of type {code_repo.repo_type}.")

        db.insert(DataProductState(
            name=data.name,
            domain=data.domain,
            description=data.description,
            admin_emails=data.admin_emails,
        ))
        db.flush()

        code_repo.create_repository(
            name=f"dp-{data.name}",
            description=f"Data product repository for {data.name}",
            fork="_template",
        )

        db.update(
            data.name,
            code_repository=True,
        )
        db.flush()


@app.get("/data-product")
def _list():
    """List all data products."""
    logger.info("Listing data products.")
    with LocalDB() as db:
        return {
            "data_products": [dp_name for dp_name in db.data_products.keys()]
        }
