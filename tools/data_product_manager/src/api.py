import os
import sys
import json
import logging
import builtins
from enum import Enum
from typing import List, Annotated, Dict, Any, Iterable, Optional

import fastapi
import pydantic
from keycloak import KeycloakAdmin
from keycloak.exceptions import KeycloakGetError, KeycloakPostError

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


class KeycloakMasterBootstrapData(pydantic.BaseModel):
    keycloak_base_url: Annotated[str, pydantic.Field(
        description="Base URL of the Keycloak server, e.g., https://auth.example.com/",
    )] = "https://auth.127.0.0.1.nip.io/"
    admin_username: Annotated[str, pydantic.Field(
        description="Keycloak admin username",
    )] = "admin"
    admin_password: Annotated[str, pydantic.Field(
        description="Keycloak admin password",
    )] = "admin"
    master_realm: Annotated[str, pydantic.Field(
        description="Keycloak master realm name",
    )] = "master"
    verify_tls: Annotated[bool, pydantic.Field(
        description="Whether to verify TLS certificates",
    )] = True
    trino_client_secret: Annotated[str, pydantic.Field(
        description="Client secret for the Trino client",
    )] = "REPLACE_WITH_SECURE_SECRET"
    trino_redirect_uris: Annotated[Optional[List[str]], pydantic.Field(
        description="List of redirect URIs for the Trino client",
    )] = ["https://trino.127.0.0.1.nip.io/oauth2/callback"]
    trino_post_logout_uris: Annotated[str, pydantic.Field(
        description="Post-logout redirect URIs for the Trino client",
    )] = "https://trino.127.0.0.1.nip.io/*"


@app.post("/keycloak/bootstrap-master")
def _bootstrap_keycloak_master(data: KeycloakMasterBootstrapData) -> Dict[str, Any]:
    """
    Run the full bootstrap in one go. Returns a summary dict.
    """
    # defaults
    if data.trino_redirect_uris is None:
        data.trino_redirect_uris = ["https://trino.127.0.0.1.nip.io/oauth2/callback"]

    # ----------------------
    # Connect (scoped to master)
    # ----------------------
    try:
        kc = KeycloakAdmin(
            server_url=data.keycloak_base_url,  # no '/auth' for KC >= 17
            username=data.admin_username,
            password=data.admin_password,
            realm_name=data.master_realm,      # ALL ops in master
            user_realm_name=data.master_realm,
            client_id="admin-cli",
            verify=data.verify_tls,
        )
        logger.info("Connected to Keycloak. Realm context: '%s'", data.master_realm)
    except Exception as e:
        logger.critical("Unable to connect to Keycloak: %s", e)
        raise

    # ----------------------
    # Helpers
    # ----------------------
    def _is_conflict(e):   return getattr(e, "response_code", None) == 409

    def _already_exists(e):
        msg = (getattr(e, "error_message", "") or str(e) or "").lower()
        return any(s in msg for s in ["already exist", "already exists", "already assigned", "exists"])

    def _silent(func, *args, **kwargs):
        """Run func, swallow 409/'already exists' and log at DEBUG. Re-raise others."""
        try:
            return func(*args, **kwargs)
        except (KeycloakGetError, KeycloakPostError) as e:
            if _is_conflict(e) or _already_exists(e):
                logger.debug("Ignored benign conflict: %s", e)
                return None
            logger.error("Call failed: %s(%s, %s): %s", func.__name__, args, kwargs, e)
            raise

    # ----------------------
    # Groups (in master)
    # ----------------------
    def _get_group_by_path(path: str):
        try:
            g = kc.get_group_by_path(path) or {}
            return g if isinstance(g, dict) else {}
        except (KeycloakGetError, KeycloakPostError):
            return {}

    def _get_groups(parent_id=None):
        try:
            return (kc.get_groups() if parent_id is None else kc.get_group_children(parent_id)) or []
        except (KeycloakGetError, KeycloakPostError):
            return []

    def _find_group_id_by_segments(segments: Iterable[str]):
        segs = list(segments)
        if not segs:
            return None
        path = "/" + "/".join(segs)
        g = _get_group_by_path(path)
        if g.get("id"):
            return g["id"]
        parent = None
        for name in segs:
            match = next((x for x in _get_groups(parent) if x.get("name") == name), None)
            if not match:
                return None
            parent = match.get("id")
        return parent

    def ensure_group(segments: Iterable[str]):
        segs = list(segments)
        gid = _find_group_id_by_segments(segs)
        if gid:
            logger.debug("Group exists: /%s", "/".join(segs))
            return gid
        parent_id = ensure_group(segs[:-1]) if len(segs) > 1 else None
        _silent(kc.create_group, {"name": segs[-1]}, parent=parent_id)
        gid = _find_group_id_by_segments(segs)
        logger.info(("Created" if gid else "Failed to verify") + " group: /%s", "/".join(segs))
        return gid

    for segs in (
        ["country"], ["country", "france"],
        ["data"], ["data", "public"], ["data", "sensitive"],
        ["region"], ["region", "eu"], ["region", "us"],
    ):
        ensure_group(segs)

    # ----------------------
    # Client scope 'groups' (in master)
    # ----------------------
    def _get_client_scope_id(name: str):
        for cs in kc.get_client_scopes() or []:
            if cs.get("name") == name:
                return cs.get("id")
        return None

    groups_scope_id = _get_client_scope_id("groups")
    if not groups_scope_id:
        logger.info("Creating client scope 'groups' (master)...")
        groups_scope_id = _silent(kc.create_client_scope, {
            "name": "groups",
            "protocol": "openid-connect",
            "attributes": {
                "include.in.token.scope": "true",
                "display.on.consent.screen": "true"
            },
            "protocolMappers": [{
                "name": "groups",
                "protocol": "openid-connect",
                "protocolMapper": "oidc-group-membership-mapper",
                "consentRequired": False,
                "config": {
                    "full.path": "true",
                    "id.token.claim": "true",
                    "access.token.claim": "true",
                    "userinfo.token.claim": "true",
                    "introspection.token.claim": "true",
                    "claim.name": "groups",
                    "lightweight.claim": "false"
                }
            }]
        }) or _get_client_scope_id("groups")
    else:
        logger.debug("Client scope 'groups' already exists (master).")

    # Add 'groups' as an OPTIONAL realm-level client scope
    try:
        current_optional = kc.get_default_optional_client_scopes() or []
        if any(s.get("name") == "groups" for s in current_optional):
            logger.info("'groups' already present in realm '%s' default-optional client scopes.", data.master_realm)
        else:
            if not groups_scope_id:
                raise RuntimeError("Client scope 'groups' id not found.")
            logger.info("Adding 'groups' to realm '%s' default-optional client scopes...", data.master_realm)
            kc.add_default_optional_client_scope(groups_scope_id)
            after = kc.get_default_optional_client_scopes() or []
            logger.debug({"defaultOptionalClientScopes": [s.get("name") for s in after]})
            if any(s.get("name") == "groups" for s in after):
                logger.info("Added 'groups' to realm '%s' default-optional client scopes.", data.master_realm)
            else:
                logger.warning("Realm '%s': 'groups' still not listed in default-optional client scopes.", data.master_realm)
    except (KeycloakGetError, KeycloakPostError) as e:
        if getattr(e, "response_code", None) == 409:
            logger.debug("Benign conflict while adding default-optional client scope: %s", e)
        else:
            logger.error("Failed to ensure 'groups' in default-optional client scopes: %s", e)
            raise

    # ----------------------
    # Client 'trino' (in master)
    # ----------------------
    def _get_client_internal_id(client_id_str):
        for c in kc.get_clients() or []:
            if c.get("clientId") == client_id_str:
                return c.get("id")
        return None

    trino_id = _get_client_internal_id("trino")
    if not trino_id:
        logger.info("Creating client 'trino' (master)...")
        trino_id = _silent(kc.create_client, {
            "clientId": "trino",
            "name": "trino",
            "enabled": True,
            "protocol": "openid-connect",
            "publicClient": False,
            "clientAuthenticatorType": "client-secret",
            "secret": data.trino_client_secret,
            "redirectUris": data.trino_redirect_uris,
            "webOrigins": ["/*"],
            "standardFlowEnabled": True,
            "implicitFlowEnabled": True,
            "serviceAccountsEnabled": True,
            "authorizationServicesEnabled": True,
            "frontchannelLogout": True,
            "attributes": {
                "oidc.ciba.grant.enabled": "true",
                "oauth2.device.authorization.grant.enabled": "true",
                "frontchannel.logout.session.required": "true",
                "backchannel.logout.session.required": "true",
                "standard.token.exchange.enabled": "true",
                "post.logout.redirect.uris": data.trino_post_logout_uris,
            },
            "defaultClientScopes": [
                "web-origins", "service_account", "acr", "roles",
                "profile", "groups", "basic", "email"
            ],
            "optionalClientScopes": [
                "address", "phone", "organization", "offline_access", "microprofile-jwt"
            ],
        }) or _get_client_internal_id("trino")
    else:
        logger.debug("Client 'trino' already exists (master).")
        _silent(kc.update_client, trino_id, {
            "enabled": True,
            "redirectUris": data.trino_redirect_uris,
            "webOrigins": ["/*"],
            "authorizationServicesEnabled": True,
            "frontchannelLogout": True,
            "attributes": {
                "oidc.ciba.grant.enabled": "true",
                "oauth2.device.authorization.grant.enabled": "true",
                "frontchannel.logout.session.required": "true",
                "backchannel.logout.session.required": "true",
                "standard.token.exchange.enabled": "true",
                "post.logout.redirect.uris": data.trino_post_logout_uris,
            },
        })

    # ----------------------
    # Authorization Services defaults (on 'trino')
    # ----------------------
    def ensure_authz_defaults(client_internal_id):
        try:
            authz = kc.get_client_authz_settings(client_internal_id) or {}
            if authz.get("resources"):
                logger.debug("AuthZ defaults already exist for 'trino' (master).")
                return
        except (KeycloakGetError, KeycloakPostError):
            pass

        logger.info("Creating AuthZ defaults for 'trino' (master)...")
        _silent(kc.create_client_authz_resource, client_internal_id, {
            "name": "Default Resource",
            "type": "urn:trino:resources:default",
            "uris": ["/*"],
            "ownerManagedAccess": False,
            "attributes": {}
        })
        _silent(kc.create_client_authz_policy, client_internal_id, {
            "name": "Default Policy",
            "type": "js",
            "logic": "POSITIVE",
            "decisionStrategy": "AFFIRMATIVE",
            "config": {"code": "$evaluation.grant();\n"}
        })
        _silent(kc.create_client_authz_permission, client_internal_id, {
            "name": "Default Permission",
            "type": "resource",
            "logic": "POSITIVE",
            "decisionStrategy": "UNANIMOUS",
            "config": {
                "resources": [],
                "applyPolicies": ["Default Policy"],
                "defaultResourceType": "urn:trino:resources:default"
            }
        })

    if trino_id:
        ensure_authz_defaults(trino_id)

    # ----------------------
    # Service account role (uma_protection) for 'trino'
    # ----------------------
    if trino_id:
        try:
            svc_user = kc.get_client_service_account_user(trino_id)
            roles = kc.get_client_roles(trino_id) or []
            if "uma_protection" not in [r.get("name") for r in roles]:
                _silent(kc.create_client_role, trino_id, {"name": "uma_protection"})
            role = kc.get_client_role(trino_id, "uma_protection")
            _silent(kc.assign_client_role, user_id=svc_user["id"], client_id=trino_id, roles=[role])
            logger.info("Ensured service-account has role 'uma_protection' (master).")
        except Exception as e:
            logger.error("Failed to ensure service-account role: %s", e)
            raise

    # ----------------------
    # Minimal OTP policy (master)
    # ----------------------
    _silent(kc.update_realm, data.master_realm, {
        "otpPolicyType": "totp",
        "otpPolicyAlgorithm": "HmacSHA1",
        "otpPolicyDigits": 6,
        "otpPolicyPeriod": 30,
        "otpPolicyLookAheadWindow": 1,
    })
    logger.info("OTP policy applied (master).")
    logger.info("✅ Done. All entities ensured in realm '%s'.", data.master_realm)

    return fastapi.Response(status_code=204)
