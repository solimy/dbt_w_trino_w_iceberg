"""Validate the Git manifest and derive Keycloak and OPA inputs."""

import hashlib
import hmac
import re
from pathlib import Path

ENVIRONMENTS = ("dev", "prd")
SQL_ADMIN = "platform-sql"
CONSUMER = "pilot-consumer"
DEMO_PRODUCT = "ventes/commandes"
DEMO_OUTPUT = "mart_country_daily_sales_eur"


def local_console_users(manifest, passwords):
    users = {}
    for username, profile in manifest.get("local_users", {}).items():
        if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]*", username) or username in {"console-finance", "console-owner"} or username.startswith("service-account-"):
            raise ValueError(f"Invalid local Keycloak username: {username!r}")
        fields = {name: profile.get(name) for name in ("email", "first_name", "last_name")}
        if any(not isinstance(value, str) or not value.strip() for value in fields.values()):
            raise ValueError(f"Local user {username} requires email, first_name and last_name")
        password = passwords.get(username)
        if not isinstance(password, str) or not password:
            raise ValueError(f"Missing initial password for local user {username}")
        users[username] = {**fields, "password": password, "temporary": True}
    return users


def console_fixture(manifest):
    product = manifest["products"].get(DEMO_PRODUCT)
    consumer_team = manifest.get("consumer_team")
    if not product or not consumer_team or DEMO_OUTPUT not in product["outputs"]:
        return {}
    domain = manifest["domains"][DEMO_PRODUCT.split("/")[0]]
    return {
        "console-finance": (consumer_team, "members"),
        "console-owner": (product.get("owner_team", domain["owner_team"]), "maintainers"),
    }


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-z][a-z0-9]*(?:_[a-z0-9]+)*", value):
        raise ValueError(f"Invalid identifier: {value!r}")
    return value


def secret_for(root, name):
    return hmac.new(root.encode(), name.encode(), hashlib.sha256).hexdigest()


def product_targets(manifest):
    for key, product in sorted(manifest["products"].items()):
        domain, separator, name = key.partition("/")
        if not separator:
            raise ValueError(f"Expected a domain/product key: {key!r}")
        identifier(domain)
        identifier(name)
        source = Path(product["dbt_project"])
        if source.is_absolute() or ".." in source.parts or source.parts[:1] != ("data_products",):
            raise ValueError(f"Invalid dbt project path for {key}")
        for environment in ENVIRONMENTS:
            yield key, product, environment, f"mydata_{environment}", f"{domain}__{name}", f"dbt-{domain}-{name}-{environment}"


def group(team, role):
    return f"/teams/{team}/{role}"


def compile_platform(manifest, root_secret, *, grant_consumers=True):
    teams = manifest["teams"]
    for name, roles in teams.items():
        identifier(name)
        for role in ("members", "maintainers"):
            users = roles[role]
            if not isinstance(users, list) or any(not isinstance(user, str) or not user.strip() for user in users):
                raise ValueError(f"{name}/{role} must list Keycloak usernames")

    def team_path(name, role):
        if name not in teams:
            raise ValueError(f"Unknown team: {name!r}")
        return group(name, role)

    for name, domain in manifest["domains"].items():
        identifier(name)
        team_path(domain["owner_team"], "maintainers")

    clients = {}
    schemas = {}

    def client(name, membership=None):
        if name in clients:
            raise ValueError(f"Duplicate Keycloak client ID: {name}")
        clients[name] = {"secret": secret_for(root_secret, name), "group": membership}

    client(SQL_ADMIN)
    if manifest.get("consumer_team"):
        client(CONSUMER, team_path(manifest["consumer_team"], "members"))

    for key, product, environment, catalog, schema, producer in product_targets(manifest):
        domain = key.split("/")[0]
        if domain not in manifest["domains"]:
            raise ValueError(f"Unknown domain: {domain!r}")
        owner = product.get("owner_team", manifest["domains"][domain]["owner_team"])
        maintainers = team_path(owner, "maintainers")
        client(producer)
        writers = [f"service-account-{producer}"]
        schemas[f"{catalog}.{schema}_internal"] = {
            "writers": writers, "readers": [maintainers] if environment == "dev" else [], "outputs": {},
        }
        outputs = {}
        for name, output in product["outputs"].items():
            identifier(name)
            readers = sorted({team_path(team, "members") for team in output["reader_teams"]})
            masks = {identifier(column): {"unmasked_groups": []} for column in output.get("masked_columns", [])}
            outputs[name] = {"readers": readers if grant_consumers and environment == "prd" else [], "masks": masks}
        schemas[f"{catalog}.{schema}_public"] = {"writers": writers, "readers": [], "outputs": outputs}

    return {"teams": teams, "clients": clients}, {"admins": [f"service-account-{SQL_ADMIN}"], "schemas": schemas}
