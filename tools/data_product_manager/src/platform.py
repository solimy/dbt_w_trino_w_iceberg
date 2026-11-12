"""Apply a data platform manifest and verify its running revision."""

import argparse
import copy
import fcntl
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from src.platform_manifest import (
    CONSUMER, DEMO_OUTPUT, DEMO_PRODUCT, SQL_ADMIN, compile_platform,
    console_fixture, local_console_users, product_targets, secret_for,
)

REPO = Path("/repo")
CONFIG = REPO / "config/platform"
STATE = Path("/state")
BUNDLES = Path("/bundles")
CA = "/ca/root.crt"
KEYCLOAK = "http://keycloak:8080"
REALM = "data-platform"
BOOTSTRAP = "platform-bootstrap"
PROVISIONER = "platform-provisioner"
TRINO_HOST = "platform-trino.127.0.0.1.nip.io"
CONSOLE_HOST = "sql.127.0.0.1.nip.io"
OPA = "http://opa:8181"
DBT_EXCLUDED = {".git", ".venv", ".user.yml", ".DS_Store", "profiles.yml", "target", "logs", "dbt_packages"}


def cached_policy(path, active_revision):
    if active_revision and path.exists():
        record = json.loads(path.read_text())
        if record.get("revision") == active_revision:
            return record["policy"]
    return {}


def staged_policy(previous, desired):
    staged = copy.deepcopy(desired)
    for name, schema in staged["schemas"].items():
        old_schema = previous.get("schemas", {}).get(name, {})
        schema["readers"] = sorted(set(schema["readers"]).intersection(old_schema.get("readers", [])))
        for table, output in schema["outputs"].items():
            old_output = old_schema.get("outputs", {}).get(table, {})
            output["readers"] = sorted(set(output["readers"]).intersection(old_output.get("readers", [])))
            output["masks"] = {**output["masks"], **old_output.get("masks", {})}
    return staged


def request(url, *, token=None, fields=None, method=None):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    data = urlencode(fields).encode() if fields is not None else None
    with urlopen(Request(url, data=data, headers=headers, method=method), timeout=10) as response:
        body = response.read()
    return json.loads(body) if body else None


def wait_for(predicate, description, *, timeout=120):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            result = predicate()
            if result:
                return result
        except (URLError, TimeoutError, ConnectionError):
            pass
        time.sleep(2)
    raise RuntimeError(f"Timed out waiting for {description}")


def run(*args, env=None, cwd=None, allowed=(0,)):
    result = subprocess.run(args, env=env, cwd=cwd, check=False)
    if result.returncode not in allowed:
        raise RuntimeError(f"{args[0]} {args[1]} failed with exit code {result.returncode}")
    return result.returncode


class Platform:
    def __init__(self, manifest, *, smoke=False):
        self.manifest = manifest
        self.root_secret = os.environ["PLATFORM_SECRET"]
        self.port = int(os.environ.get("PLATFORM_PORT", "8443"))
        self.identity, self.policy = compile_platform(manifest, self.root_secret)
        passwords = (
            {name: self.secret(f"local-user-{name}") for name in manifest.get("local_users", {})}
            if smoke else json.loads(os.environ.get("PLATFORM_LOCAL_USER_PASSWORDS") or "{}")
        )
        self.console_users = local_console_users(manifest, passwords)
        if smoke:
            self.identity["teams"] = copy.deepcopy(self.identity["teams"])
            for username, (team, role) in console_fixture(manifest).items():
                self.console_users[username] = {
                    "email": f"{username}@example.test", "first_name": username, "last_name": "Pilot",
                    "password": self.secret(username), "temporary": False,
                }
                members = self.identity["teams"][team][role]
                if username not in members:
                    members.append(username)

    def secret(self, name):
        return secret_for(self.root_secret, name)

    def token(self, client, realm=REALM):
        payload = request(f"{KEYCLOAK}/realms/{realm}/protocol/openid-connect/token", fields={
            "grant_type": "client_credentials",
            "client_id": client,
            "client_secret": self.secret(client),
        })
        return payload["access_token"]

    def provisioner_ready(self):
        try:
            token = self.token(PROVISIONER)
            request(f"{KEYCLOAK}/admin/realms/{REALM}", token=token)
        except HTTPError as error:
            if error.code in (401, 403, 404):
                return False
            raise
        return True

    def tofu(self, layer, action, *args):
        source = CONFIG / "keycloak" / layer
        target = STATE / "keycloak" / layer
        target.mkdir(parents=True, exist_ok=True)
        for path in target.glob("*.tf"):
            path.unlink()
        for path in source.glob("*.tf"):
            shutil.copyfile(path, target / path.name)
        realm, client = ("master", BOOTSTRAP) if layer == "bootstrap" else (REALM, PROVISIONER)
        environment = {
            **os.environ, "TF_IN_AUTOMATION": "1",
            "KEYCLOAK_URL": KEYCLOAK, "KEYCLOAK_REALM": realm,
            "KEYCLOAK_CLIENT_ID": client, "KEYCLOAK_CLIENT_SECRET": self.secret(client),
            "TF_VAR_provisioner_secret": self.secret(PROVISIONER),
            "TF_VAR_platform": json.dumps(self.identity),
            "TF_VAR_trino_client_secret": self.secret("trino"),
            "TF_VAR_trino_url": f"https://{TRINO_HOST}:{self.port}",
            "TF_VAR_query_console_url": f"https://{CONSOLE_HOST}:{self.port}",
            "TF_VAR_query_console_secret": self.secret("query-console"),
            "TF_VAR_query_console_users": json.dumps(self.console_users),
        }
        run("tofu", "init", "-input=false", "-no-color", cwd=target, env=environment)
        return run("tofu", action, "-input=false", "-no-color", *args, cwd=target, env=environment, allowed=(0, 2) if action == "plan" else (0,))

    def reconcile_keycloak(self):
        wait_for(lambda: request(f"{KEYCLOAK}/realms/master/.well-known/openid-configuration"), "Keycloak")
        if not self.provisioner_ready():
            try:
                self.token(BOOTSTRAP, "master")
            except HTTPError as error:
                raise RuntimeError("Keycloak provisioner unavailable. Recover the temporary bootstrap account before applying.") from error
            self.tofu("bootstrap", "apply", "-auto-approve")
            wait_for(self.provisioner_ready, "the realm provisioner")
        try:
            token = self.token(BOOTSTRAP, "master")
        except HTTPError as error:
            if error.code not in (401, 404):
                raise
        else:
            url = f"{KEYCLOAK}/admin/realms/master/clients"
            clients = request(f"{url}?{urlencode({'clientId': BOOTSTRAP})}", token=token)
            client = next(item for item in clients if item["clientId"] == BOOTSTRAP)
            request(f"{url}/{client['id']}", token=token, method="DELETE")
        self.tofu("managed", "apply", "-auto-approve")

    def bundle_revision(self, policy):
        digest = hashlib.sha256(json.dumps(policy, sort_keys=True).encode())
        for path in sorted((CONFIG / "opa").glob("*.rego")):
            if not path.name.endswith("_test.rego"):
                digest.update(path.read_bytes())
        return f"{os.environ['PLATFORM_REVISION']}-{digest.hexdigest()[:16]}"

    def active_revision(self):
        return request(f"{OPA}/v1/data/platform/revision").get("result")

    def publish(self, policy):
        revision = self.bundle_revision(policy)
        destination = BUNDLES / "platform.tar.gz"
        if not destination.exists() or self.active_revision() != revision:
            with tempfile.TemporaryDirectory(dir=BUNDLES) as temp:
                staging = Path(temp) / "bundle"
                staging.mkdir()
                for path in (CONFIG / "opa").glob("*.rego"):
                    if not path.name.endswith("_test.rego"):
                        shutil.copyfile(path, staging / path.name)
                (staging / ".manifest").write_text(json.dumps({"roots": ["trino", "platform"], "revision": revision}))
                (staging / "data.json").write_text(json.dumps({"platform": {**policy, "revision": revision}}))
                artifact = Path(temp) / "platform.tar.gz"
                run("opa", "build", "--bundle", str(staging), "--output", str(artifact))
                artifact.chmod(0o644)
                artifact.replace(destination)
            wait_for(lambda: self.active_revision() == revision, f"OPA bundle {revision}")
        policy_file = STATE / "active-policy.json"
        temporary = policy_file.with_suffix(".tmp")
        temporary.write_text(json.dumps({"revision": revision, "policy": policy}))
        temporary.replace(policy_file)
        print(f"OPA active revision: {revision}", flush=True)

    def query(self, client, sql, *, token=None):
        from trino.auth import JWTAuthentication
        from trino.dbapi import connect

        with connect(host=TRINO_HOST, port=self.port, http_scheme="https", verify=CA,
                     user=f"service-account-{client}", auth=JWTAuthentication(token or self.token(client)),
                     max_attempts=1, request_timeout=15) as connection:
            cursor = connection.cursor()
            cursor.execute(sql)
            return cursor.fetchall()

    def wait_for_trino(self):
        import requests

        def ready():
            try:
                response = requests.get(f"https://{TRINO_HOST}:{self.port}/v1/info", verify=CA, timeout=5)
                return response.ok and not response.json().get("starting", True)
            except requests.RequestException:
                return False

        wait_for(ready, "Trino", timeout=180)

    def product_fingerprint(self, source, outputs):
        digest = hashlib.sha256(json.dumps(outputs, sort_keys=True).encode())
        for folder, directories, filenames in os.walk(source):
            directories[:] = sorted(set(directories) - DBT_EXCLUDED)
            for filename in sorted(set(filenames) - DBT_EXCLUDED):
                path = Path(folder) / filename
                digest.update(str(path.relative_to(source)).encode() + b"\0")
                digest.update(hashlib.sha256(path.read_bytes()).digest())
        return digest.hexdigest()

    def build_product(self, key, product, environment, catalog, schema, producer):
        source = (REPO / product["dbt_project"]).resolve()
        if not source.is_relative_to(REPO / "data_products") or not (source / "dbt_project.yml").is_file():
            raise ValueError(f"Invalid dbt project for {key}")
        outputs = product["outputs"]
        fingerprint = self.product_fingerprint(source, sorted(outputs))
        record = STATE / f"{producer}.sha256"
        if record.exists() and record.read_text() == fingerprint and self.outputs_exist(catalog, schema, outputs):
            self.validate_masked_columns(producer, f"{catalog}.{schema}_public", outputs)
            print(f"Product already built: {key} ({environment})", flush=True)
            return

        with tempfile.TemporaryDirectory() as temp:
            profile = Path(temp)
            checkout = profile / "project"
            shutil.copytree(source, checkout, ignore=shutil.ignore_patterns(*DBT_EXCLUDED))
            profile_config = {"default": {"target": environment, "outputs": {environment: {
                "type": "trino", "method": "jwt", "jwt_token": "{{ env_var('DBT_ENV_SECRET_TRINO_TOKEN') }}",
                "user": f"service-account-{producer}", "host": TRINO_HOST, "port": self.port,
                "cert": CA, "database": catalog, "schema": f"{schema}_internal",
            }}}}
            (profile / "profiles.yml").write_text(json.dumps(profile_config))
            dbt_environment = {
                "PATH": os.environ["PATH"], "DBT_ENV_SECRET_TRINO_TOKEN": self.token(producer),
                "REQUESTS_CA_BUNDLE": CA, "DBT_SEND_ANONYMOUS_USAGE_STATS": "false",
            }
            dbt_args = ("--project-dir", str(checkout), "--profiles-dir", temp, "--profile", "default")
            if any((checkout / name).exists() for name in ("packages.yml", "dependencies.yml")):
                run("dbt", "deps", *dbt_args, env=dbt_environment)
            run("dbt", "build", *dbt_args,
                "--vars", '{"platform_materialization":"table"}', env=dbt_environment)
            nodes = json.loads((checkout / "target/manifest.json").read_text())["nodes"]
            models = {node["alias"] for node in nodes.values() if node["resource_type"] == "model"
                      and node["database"] == catalog and node["schema"] == f"{schema}_internal"}
            if not outputs.keys() <= models:
                raise ValueError(f"Declared outputs are missing from the built dbt models: {sorted(outputs.keys() - models)}")

        self.validate_masked_columns(producer, f"{catalog}.{schema}_internal", outputs)
        for table in outputs:
            self.query(producer, f"CREATE OR REPLACE TABLE {catalog}.{schema}_public.{table} "
                                 f"AS SELECT * FROM {catalog}.{schema}_internal.{table}")
        record.write_text(fingerprint)

    def validate_masked_columns(self, producer, schema, outputs):
        for name, output in outputs.items():
            masked = set(output.get("masked_columns", []))
            if masked:
                columns = {row[0] for row in self.query(producer, f"SHOW COLUMNS FROM {schema}.{name}")}
                if missing := masked - columns:
                    raise ValueError(f"Unknown masked columns in {schema}.{name}: {sorted(missing)}")

    def outputs_exist(self, catalog, schema, outputs):
        visible = self.query(SQL_ADMIN, f"SHOW TABLES FROM {catalog}.{schema}_public")
        return outputs.keys() <= {row[0] for row in visible}

    def apply(self):
        wait_for(lambda: isinstance(request(f"{OPA}/v1/data/platform/revision"), dict), "OPA")
        previous = cached_policy(STATE / "active-policy.json", self.active_revision())
        self.publish(staged_policy(previous, self.policy))
        self.reconcile_keycloak()
        self.wait_for_trino()
        for target in product_targets(self.manifest):
            _, _, environment, catalog, schema, _ = target
            for kind in ("internal", "public"):
                namespace = f"{schema}_{kind}"
                self.query(SQL_ADMIN, f"CREATE SCHEMA IF NOT EXISTS {catalog}.{namespace} "
                                      f"WITH (location = 's3://warehouse-{environment}/{namespace}/')")
            self.build_product(*target)
        self.publish(self.policy)

    def drift(self):
        drift = self.tofu("managed", "plan", "-detailed-exitcode") == 2
        realm = request(f"{KEYCLOAK}/admin/realms/{REALM}", token=self.token(PROVISIONER))
        if realm.get("accessTokenLifespan") != 300:
            print("Keycloak access token lifetime differs from the bootstrap configuration.", file=sys.stderr)
            drift = True
        if self.active_revision() != self.bundle_revision(self.policy):
            print("OPA bundle revision differs from the desired revision.", file=sys.stderr)
            drift = True
        for key, product, _, catalog, schema, producer in product_targets(self.manifest):
            namespaces = {row[0] for row in self.query(SQL_ADMIN, f"SHOW SCHEMAS FROM {catalog}")}
            if not {f"{schema}_public", f"{schema}_internal"} <= namespaces or not self.outputs_exist(catalog, schema, product["outputs"]):
                print(f"Missing Iceberg schemas or outputs for {key} in {catalog}.", file=sys.stderr)
                drift = True
            record = STATE / f"{producer}.sha256"
            fingerprint = self.product_fingerprint(REPO / product["dbt_project"], sorted(product["outputs"]))
            if not record.exists() or record.read_text() != fingerprint:
                print(f"dbt inputs differ from the last published build for {key} in {catalog}.", file=sys.stderr)
                drift = True
        return 2 if drift else 0

    def smoke(self):
        run("opa", "test", str(CONFIG / "opa"))
        self.apply()
        if self.drift():
            raise RuntimeError("The deployed platform does not match the manifest")

        fixture = next((target for target in product_targets(self.manifest)
                        if target[0] == DEMO_PRODUCT and target[2] == "prd"
                        and DEMO_OUTPUT in target[1]["outputs"]), None)
        if CONSUMER not in self.identity["clients"] or fixture is None:
            path = REPO / "tools/data_product_manager/tests/fixtures/platform.json"
            demo = Platform(json.loads(path.read_text()), smoke=True)
            demo.apply()
            demo.check_permissions(next(target for target in product_targets(demo.manifest) if target[2] == "prd"), DEMO_OUTPUT)
            return
        self.check_permissions(fixture, DEMO_OUTPUT)

    def check_permissions(self, fixture, output):
        from trino.exceptions import TrinoQueryError

        _, _, _, catalog, schema, producer = fixture
        public = f"{catalog}.{schema}_public.{output}"
        internal = f"{catalog}.{schema}_internal.{output}"
        token = self.token(CONSUMER)

        def denied(sql):
            try:
                self.query(CONSUMER, sql, token=token)
            except TrinoQueryError as error:
                if error.error_name == "PERMISSION_DENIED":
                    return
                raise
            raise RuntimeError("Trino allowed a query that must be denied")

        denied(f"SELECT * FROM {internal}")
        group = self.identity["clients"][CONSUMER]["group"]
        desired_output = self.policy["schemas"][f"{catalog}.{schema}_public"]["outputs"][output]
        if group not in desired_output["readers"]:
            denied(f"SELECT * FROM {public}")

        granted = copy.deepcopy(self.policy)
        granted_output = granted["schemas"][f"{catalog}.{schema}_public"]["outputs"][output]
        granted_output["readers"] = [group]
        granted_output["masks"]["country_name"] = {"unmasked_groups": []}
        try:
            self.publish(granted)
            source = self.query(producer, f"SELECT count(*), count(country_name) FROM {internal}")[0]
            masked = self.query(CONSUMER, f"SELECT count(*), count(country_name) FROM {public}", token=token)[0]
            if source[0] <= 0 or source[1] <= 0 or list(masked) != [source[0], 0]:
                raise RuntimeError("Expected nonempty output and a column mask for the consumer")
            revoked = copy.deepcopy(granted)
            revoked["schemas"][f"{catalog}.{schema}_public"]["outputs"][output]["readers"] = []
            self.publish(revoked)
            denied(f"SELECT * FROM {public}")
        finally:
            self.publish(self.policy)
        print("Platform smoke passed: dbt build, consumer mask, internal denial, revocation with the same token.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("apply", "plan", "drift", "smoke"))
    args = parser.parse_args()
    os.umask(0o077)
    if args.action == "smoke" and not os.environ.get("PLATFORM_PROJECT", "").startswith("data-platform-smoke"):
        parser.error("smoke requires an isolated data-platform-smoke project")
    platform = Platform(json.loads((CONFIG / "platform.json").read_text()), smoke=args.action == "smoke")
    with (STATE / "run.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if args.action == "plan":
            return platform.tofu("managed", "plan", "-detailed-exitcode")
        return getattr(platform, args.action)() or 0


if __name__ == "__main__":
    sys.exit(main())
