"""Verify browser SSO and SQL authorization against the disposable pilot."""

import json
import os
import re
from pathlib import Path
from urllib.parse import urlencode, urljoin, urlsplit

from playwright.sync_api import sync_playwright
from tools.data_product_manager.src.platform_manifest import (
    DEMO_OUTPUT,
    DEMO_PRODUCT,
    console_fixture,
    secret_for,
)

REPO = Path("/repo")
ORIGIN = f"https://sql.127.0.0.1.nip.io:{os.environ['PLATFORM_PORT']}"
ISSUER = f"https://platform-auth.127.0.0.1.nip.io:{os.environ['PLATFORM_PORT']}"
SQL_HEADERS = {"Origin": ORIGIN, "Content-Type": "text/plain"}
MANIFEST = REPO / "config/platform/platform.json"
FALLBACK = REPO / "tools/data_product_manager/tests/fixtures/platform.json"


def login(browser, username):
    # The Compose smoke project issues a disposable local Caddy CA.
    context = browser.new_context(base_url=ORIGIN, ignore_https_errors=True)
    page = context.new_page()
    page.goto("/")
    assert page.url.startswith(f"{ISSUER}/realms/data-platform/"), "Login did not go to Keycloak"
    page.locator("#username").fill(username)
    page.locator("#password").fill(secret_for(os.environ["PLATFORM_SECRET"], username))
    page.locator("#kc-login").click()
    page.wait_for_url(f"{ORIGIN}/")
    session = context.request.get("/api/session")
    assert session.ok and session.json() == {"user": username}, "Console session did not preserve the SSO identity"
    assert any(cookie["httpOnly"] and cookie["secure"] for cookie in context.cookies(ORIGIN))
    return context, page


def query(context, sql, *, headers=None):
    response = context.request.post("/v1/statement", data=sql, headers={**SQL_HEADERS, **(headers or {})})
    rows = []
    for _ in range(60):
        assert response.ok, f"Unexpected statement response {response.status}"
        payload = response.json()
        if error := payload.get("error"):
            return rows, error["errorName"]
        rows.extend(payload.get("data", []))
        if not (next_uri := payload.get("nextUri")):
            return rows, None
        next_url = urljoin(ORIGIN, next_uri)
        assert urlsplit(next_url).netloc == urlsplit(ORIGIN).netloc
        assert urlsplit(next_url).scheme == "https"
        response = context.request.get(next_url)
    raise RuntimeError("Trino did not finish returning query pages")


def verify_editor(page, username):
    page.goto("/?" + urlencode({"q": "SELECT 'actor:' || current_user AS proof"}))
    page.get_by_title(re.compile(r"^Run query")).click()
    page.get_by_text(f"actor:{username}", exact=True).wait_for(timeout=30_000)


def main():
    manifest = json.loads(MANIFEST.read_text())
    if not console_fixture(manifest):
        manifest = json.loads(FALLBACK.read_text())
    output = manifest["products"][DEMO_PRODUCT]["outputs"][DEMO_OUTPUT]
    public = f"mydata_prd.ventes__commandes_public.{DEMO_OUTPUT}"
    internal_prd = f"mydata_prd.ventes__commandes_internal.{DEMO_OUTPUT}"
    internal_dev = f"mydata_dev.ventes__commandes_internal.{DEMO_OUTPUT}"

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        guest = browser.new_context(base_url=ORIGIN, ignore_https_errors=True)
        assert guest.request.post("/v1/statement", data="SELECT 1", headers=SQL_HEADERS).status == 401

        finance, finance_page = login(browser, "console-finance")
        owner, owner_page = login(browser, "console-owner")
        verify_editor(owner_page, "console-owner")
        started = owner.request.post("/v1/statement", data="SELECT current_user", headers=SQL_HEADERS)
        assert started.ok
        continuation = started.json()["nextUri"]
        assert finance.request.get(continuation).status == 404, "Finance could poll the owner's query"
        assert owner.request.get(continuation).ok
        raw, error = query(owner, f"SELECT count(*), count(country_name) FROM {internal_dev}")
        assert error is None and raw[0][0] > 0 and raw[0][1] > 0
        assert query(owner, f"SELECT * FROM {public}")[1] == "PERMISSION_DENIED"
        assert query(finance, f"SELECT * FROM {internal_prd}")[1] == "PERMISSION_DENIED"

        if manifest["consumer_team"] in output["reader_teams"]:
            verify_editor(finance_page, "console-finance")
            rows, error = query(finance, f"SELECT count(*), count(country_name) FROM {public}", headers={
                "X-Trino-User": "console-owner",
                "X-Trino-Original-User": "console-owner",
                "Authorization": "Bearer not-the-session-token",
            })
            expected = 0 if "country_name" in output.get("masked_columns", []) else raw[0][1]
            assert error is None and rows == [[raw[0][0], expected]], "Finance received the wrong SQL permissions"
            assert query(finance, "SELECT current_user", headers={"X-Trino-User": "console-owner"}) == ([["console-finance"]], None)
        else:
            assert query(finance, f"SELECT * FROM {public}")[1] == "PERMISSION_DENIED"

        assert owner.request.post("/v1/statement", data="SELECT 1", headers={
            **SQL_HEADERS, "Origin": ISSUER,
        }).status == 403
        assert owner.request.post("/logout", headers={"Origin": ORIGIN}).status == 204
        assert owner.request.get("/api/session").status == 401
        browser.close()
    print("Console browser smoke passed: Keycloak SSO, two SQL identities, OPA grants/denials and session isolation.")


if __name__ == "__main__":
    main()
