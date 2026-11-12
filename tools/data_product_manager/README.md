# Data platform pilot

The pilot provisions the `ventes/commandes` product from `data_products/test_dp_2/dbt`. It uses a separate Compose project with its own named volumes for Keycloak, Iceberg, RustFS, OPA bundles and OpenTofu state. It listens on loopback port 8443 by default.

## Apply a manifest

Set a persistent random secret in your environment or secret manager. Reuse it for the lifetime of the project. Compose and the provisioning job derive the local service credentials from this secret.

```bash
export PLATFORM_SECRET="$(openssl rand -hex 32)"
tools/platform apply
tools/platform drift
```

If Rancher Desktop uses a private Docker socket, run `export DOCKER_HOST="unix://$HOME/.rd/docker.sock"` first. Trino is at `https://platform-trino.127.0.0.1.nip.io:8443`. The client needs to trust the pilot's Caddy CA.

Edit `config/platform/platform.json` to declare teams, their Keycloak usernames, domains, products and output grants. The sample starts with a synthetic Finance consumer for automated testing. List your users' Keycloak usernames in `members` or `maintainers`. The pilot manages these group memberships authoritatively. Use one source of membership per group.

On a fresh volume, Compose starts Keycloak with a temporary service account. OpenTofu creates the `data-platform` realm and its provisioner, then the job verifies the provisioner's access and removes the temporary account. Later applications use the realm-scoped provisioner. Keep the state volume and any backups private because the provider stores client secrets in state. The pilot starts a fresh realm in its own instance. Migrating an existing Keycloak realm requires importing the resources into state or explicitly adopting them with existing administrative credentials.

The apply publishes an intermediate bundle that removes revoked grants and withholds new ones. It then reconciles Keycloak, creates the Iceberg schemas, builds dbt with a product-specific account and materializes the declared outputs. After those steps succeed it activates the final OPA bundle. If a step fails, run the same apply again. Removed products lose their policy grants and client identities while their Iceberg data is retained. Treat dbt project changes as trusted deployment code because the build runs in the provisioning container.

The pilot uses separate JDBC catalog namespaces, RustFS instances and Trino storage credentials for dev and prd. The original Compose stack and its catalog remain separate from this pilot. The pilot's OPA policy authorizes only operations used by the product workflow and its consumers.

## SQL console

Open [the SQL console](https://sql.127.0.0.1.nip.io:8443) after `tools/platform apply`. Trust the pilot's Caddy CA in your browser and sign in with a Keycloak account from the `data-platform` realm. The URL uses `PLATFORM_PORT` if you set a different pilot port.

To export the local CA for your browser's trust store, copy its public certificate from Caddy.

```bash
docker cp "${PLATFORM_PROJECT:-data-platform}-caddy-1:/data/caddy/pki/authorities/local/root.crt" platform-ca.crt
```

OpenTofu creates the console's OIDC client. The console keeps tokens server-side and submits SQL to Trino as the signed-in user. OPA filters the visible catalogs, schemas and tables and applies the same data permissions as other Trino clients. Run `SELECT current_user` to check the SQL identity. Rebuilding the console clears its active browser sessions.

Use existing or federated Keycloak users, or declare local users under the optional top-level `local_users` key in `config/platform/platform.json`.

```json
{
  "local_users": {
    "alice": {"email": "alice@example.org", "first_name": "Alice", "last_name": "Example"}
  }
}
```

List `alice` in the appropriate team and inject `PLATFORM_LOCAL_USER_PASSWORDS` from your secret store as a JSON object mapping usernames to initial passwords. Keycloak prompts the user to replace their temporary password on first login. The deployment workflow reads the same variable from its environment secrets. Initial passwords are also stored in OpenTofu state. Removing a local user from the manifest removes the managed Keycloak account at the next apply.

## Review and drift

`tools/platform plan` previews managed Keycloak changes on a running pilot. `tools/platform drift` also checks the running service configuration, Keycloak token lifetime, OPA's active revision, dbt inputs and the existence of declared schemas and outputs. Exit code 2 means changes or drift are present. To grant Finance access, add `finance` to the output's `reader_teams`. To revoke it, remove that entry and apply. Changes to a user's group memberships take effect in newly issued tokens. The pilot configures five-minute access tokens.

The GitHub workflow verifies PRs and pushes in an isolated stack. To enable reconciliation on your infrastructure, register a trusted self-hosted runner labeled `data-platform`, set repository variable `DATA_PLATFORM_DEPLOY=true`, and configure the environment secret `PLATFORM_SECRET`. The runner must retain its Docker volumes. Pushes to `main` apply after the isolated test succeeds. The scheduled job reports drift without applying changes.

## Run the end-to-end test

```bash
export PLATFORM_PROJECT=data-platform-smoke PLATFORM_PORT=8444
export PLATFORM_SECRET="$(openssl rand -hex 32)"
tools/platform smoke
tools/platform down --volumes
```

The test builds dbt without a browser, checks its SQL access and temporarily revokes an output grant using the same token. It then signs two disposable human accounts into the console in separate Chromium sessions. Finance can read the published output with the configured mask. The owner can read the internal development schema. The test checks the denials as well and verifies that client headers cannot change the SQL identity. If the manifest removes the demo product or consumer, the test also applies an independent fixture in the disposable project.
