import json
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

import requests
from joserfc import jwt
from joserfc.jwk import RSAKey

from app import create_app


CONSOLE = "https://sql.example.test:8443"
ISSUER = "https://auth.example.test:8443/realms/platform"
TRINO = "https://trino.example.test:8443"
QUERY_ID = "20261112_120000_00001_abcde"
QUEUED = f"/v1/statement/queued/{QUERY_ID}/slug/1"
RUNNING = f"/v1/statement/executing/{QUERY_ID}/slug/2"
PARTIAL_CANCEL = f"/v1/statement/executing/partialCancel/{QUERY_ID}/1/slug/2"


def http_response(status, payload=None, headers=None):
    response = requests.Response()
    response.status_code = status
    response._content = b"" if payload is None else json.dumps(payload).encode()
    response.headers.update(headers or {})
    return response


class ConsoleTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.signing_key = RSAKey.generate_key(2048, private=True, parameters={"kid": "key"})

    def setUp(self):
        static = TemporaryDirectory()
        self.addCleanup(static.cleanup)
        Path(static.name, "index.html").write_text("<title>Test console</title>")
        env = {
            "CONSOLE_PUBLIC_URL": CONSOLE,
            "CONSOLE_ISSUER": ISSUER,
            "CONSOLE_CLIENT_SECRET": "console-client-secret",
            "CONSOLE_TRINO_URL": TRINO,
            "REQUESTS_CA_BUNDLE": "/test/ca.crt",
        }
        with patch.dict("os.environ", env):
            self.app = create_app(static_folder=static.name)
        self.app.testing = True
        self.client = self.app.test_client()
        self.access_token = "personal-access-token"
        self.nonce = ""
        self.token_overrides = {}
        self.expires_in = 300
        self.refresh_responses = []
        self.refresh_calls = []
        self.statement_response = http_response(200, {"id": QUERY_ID, "nextUri": TRINO + QUEUED})
        self.statement_calls = []
        patcher = patch.object(requests.Session, "request", side_effect=self.request_upstream)
        patcher.start()
        self.addCleanup(patcher.stop)

    def browser(self, method, path, client=None, **kwargs):
        client = self.client if client is None else client
        return client.open(path, method=method, base_url=CONSOLE, **kwargs)

    def request_upstream(self, method, url, **kwargs):
        if url == ISSUER + "/.well-known/openid-configuration":
            return http_response(200, {
                "issuer": ISSUER,
                "authorization_endpoint": ISSUER + "/auth",
                "token_endpoint": ISSUER + "/token",
                "jwks_uri": ISSUER + "/certs",
                "id_token_signing_alg_values_supported": ["RS256"],
            })
        if url == ISSUER + "/certs":
            return http_response(200, {"keys": [self.signing_key.as_dict(private=False)]})
        if url == ISSUER + "/token":
            body = kwargs["data"]
            if isinstance(body, str):
                body = {name: values[0] for name, values in parse_qs(body).items()}
            if body["grant_type"] == "refresh_token":
                self.refresh_calls.append(body["refresh_token"])
                return self.refresh_responses.pop(0)
            self.assertEqual(body["redirect_uri"], CONSOLE + "/oauth/callback")
            self.assertTrue(body["code_verifier"])
            claims = {
                "iss": ISSUER,
                "sub": "account-id",
                "aud": "query-console",
                "exp": int(time.time()) + 300,
                "iat": int(time.time()),
                "nonce": self.nonce,
                "preferred_username": "alice",
            }
            claims.update(self.token_overrides)
            id_token = jwt.encode({"alg": "RS256", "kid": "key"}, claims, self.signing_key)
            return http_response(200, {
                "access_token": self.access_token,
                "refresh_token": "private-refresh-token",
                "expires_in": self.expires_in,
                "token_type": "Bearer",
                "id_token": id_token,
            })
        if url.startswith(TRINO + "/v1/statement"):
            self.statement_calls.append((method, url, kwargs))
            return self.statement_response
        self.fail(f"Unexpected upstream request {method} {url}")

    def start_login(self, client=None):
        response = self.browser("GET", "/login", client=client)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.location.startswith(ISSUER + "/auth?"))
        params = parse_qs(urlsplit(response.location).query)
        self.assertEqual(params["client_id"], ["query-console"])
        self.assertEqual(params["redirect_uri"], [CONSOLE + "/oauth/callback"])
        self.assertEqual(params["code_challenge_method"], ["S256"])
        self.assertIn("code_challenge", params)
        self.nonce = params["nonce"][0]
        cookie = response.headers["Set-Cookie"]
        for flag in ("__Host-", "Path=/", "Secure", "HttpOnly", "SameSite=Lax"):
            self.assertIn(flag, cookie)
        return "/oauth/callback?code=valid-code&state=" + params["state"][0]

    def login(self, client=None):
        response = self.browser("GET", self.start_login(client), client=client)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.location, "/")
        self.assertNotIn(self.access_token, response.headers["Set-Cookie"])
        return response

    def test_login_uses_verified_user_and_rotates_session(self):
        self.assertEqual(self.browser("GET", "/").location, "/login")
        self.assertEqual(self.browser("GET", "/api/session").status_code, 401)
        callback = self.start_login()
        self.assertEqual(self.browser("GET", callback, client=self.app.test_client()).status_code, 400)
        response = self.browser("GET", callback)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.browser("GET", "/api/session").json, {"user": "alice"})
        with self.browser("GET", "/") as response:
            self.assertIn(b"Test console", response.data)
        self.assertEqual(self.browser("GET", callback).status_code, 400)

    def test_nonce_issuer_and_audience_must_match(self):
        for claim in ("nonce", "iss", "aud"):
            with self.subTest(claim=claim):
                client = self.app.test_client()
                self.token_overrides = {claim: "wrong"}
                response = self.browser("GET", self.start_login(client), client=client)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(self.browser("GET", "/api/session", client=client).status_code, 401)

    def test_statement_uses_personal_token_and_safe_session_headers(self):
        self.login()
        response = self.browser("POST", "/v1/statement", data="SELECT 1", headers={
            "Origin": CONSOLE,
            "X-Trino-User": "system",
            "X-Trino-Original-User": "admin",
            "X-Trino-Extra-Credential": "credential=browser",
            "X-Trino-Role": "system=ROLE{admin}",
            "X-Presto-User": "admin",
            "Authorization": "Bearer browser-token",
            "X-Forwarded-User": "admin",
            "X-Trino-Catalog": "mydata_dev",
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["nextUri"], CONSOLE + QUEUED)
        method, url, call = self.statement_calls.pop()
        self.assertEqual((method, url), ("POST", TRINO + "/v1/statement"))
        self.assertEqual(call["data"], b"SELECT 1")
        self.assertEqual(call["headers"]["X-Trino-User"], "alice")
        self.assertEqual(call["headers"]["Authorization"], "Bearer " + self.access_token)
        self.assertEqual(call["headers"]["X-Trino-Catalog"], "mydata_dev")
        self.assertNotIn("X-Trino-Original-User", call["headers"])
        self.assertNotIn("X-Trino-Extra-Credential", call["headers"])
        self.assertNotIn("X-Trino-Role", call["headers"])
        self.assertNotIn("X-Presto-User", call["headers"])
        self.assertNotIn("X-Forwarded-User", call["headers"])
        self.assertFalse(call["allow_redirects"])
        self.assertEqual(call["verify"], "/test/ca.crt")

    def test_poll_and_cancel_are_bound_to_the_submitting_session(self):
        self.login()
        self.browser("POST", "/v1/statement", headers={"Origin": CONSOLE}, data="SELECT 1")
        other_client = self.app.test_client()
        self.login(other_client)
        self.assertEqual(self.browser("GET", QUEUED, client=other_client).status_code, 404)
        self.statement_response = http_response(200, {
            "id": QUERY_ID,
            "nextUri": TRINO + RUNNING,
            "partialCancelUri": TRINO + PARTIAL_CANCEL,
        }, {"X-Trino-Set-Catalog": "mydata_dev", "Set-Cookie": "upstream-secret", "Location": TRINO})
        response = self.browser("GET", QUEUED)
        self.assertEqual(response.json["nextUri"], CONSOLE + RUNNING)
        self.assertEqual(response.json["partialCancelUri"], CONSOLE + PARTIAL_CANCEL)
        self.assertEqual(response.headers["X-Trino-Set-Catalog"], "mydata_dev")
        self.assertNotIn("Set-Cookie", response.headers)
        self.assertNotIn("Location", response.headers)
        self.statement_response = http_response(204)
        self.assertEqual(self.browser("DELETE", RUNNING).status_code, 403)
        self.assertEqual(self.browser("DELETE", RUNNING, headers={"Origin": CONSOLE}).status_code, 204)
        self.assertEqual(self.statement_calls[-1][:2], ("DELETE", TRINO + RUNNING))

    def test_rejects_mutations_without_same_origin_and_unsafe_next_uris(self):
        self.login()
        for origin in (None, "null", "https://other.example.test:8443"):
            with self.subTest(origin=origin):
                headers = {} if origin is None else {"Origin": origin}
                self.assertEqual(self.browser("POST", "/v1/statement", headers=headers).status_code, 403)
                self.assertEqual(self.browser("POST", "/logout", headers=headers).status_code, 403)
        self.assertFalse(self.statement_calls)
        for uri in (
            "https://other.example.test:8443" + QUEUED,
            TRINO + "/ui/api/query/" + QUERY_ID,
            TRINO + "/v1/statement/queued/../admin",
            TRINO + QUEUED + "#fragment",
        ):
            with self.subTest(uri=uri):
                self.statement_response = http_response(200, {"id": QUERY_ID, "nextUri": uri})
                response = self.browser("POST", "/v1/statement", headers={"Origin": CONSOLE}, data="SELECT 1")
                self.assertEqual(response.status_code, 502)

    def test_logout_revokes_the_server_session(self):
        self.login()
        cookie = self.client.get_cookie("__Host-query-console", domain=urlsplit(CONSOLE).hostname)
        response = self.browser("POST", "/logout", headers={"Origin": CONSOLE})
        self.assertEqual(response.status_code, 204)
        self.client.set_cookie(cookie.key, cookie.value, domain=cookie.domain)
        self.assertEqual(self.browser("GET", "/api/session").status_code, 401)

    def test_upstream_401_requires_sign_in_and_drops_auth_challenge(self):
        self.login()
        self.statement_response = http_response(401, headers={"WWW-Authenticate": "Bearer x_token=secret"})
        response = self.browser("POST", "/v1/statement", headers={"Origin": CONSOLE}, data="SELECT 1")
        self.assertEqual(response.status_code, 401)
        self.assertNotIn("WWW-Authenticate", response.headers)
        self.assertEqual(self.browser("GET", "/api/session").status_code, 401)

    def test_refresh_reuses_the_session_and_rotates_its_refresh_token(self):
        self.expires_in = 10
        self.login()
        self.refresh_responses.extend([
            http_response(200, {
                "access_token": "refreshed-once",
                "refresh_token": "rotated-refresh-token",
                "expires_in": 10,
                "token_type": "Bearer",
            }),
            http_response(200, {
                "access_token": "refreshed-twice",
                "expires_in": 300,
                "token_type": "Bearer",
            }),
        ])
        for token in ("refreshed-once", "refreshed-twice", "refreshed-twice"):
            response = self.browser("POST", "/v1/statement", headers={"Origin": CONSOLE}, data="SELECT 1")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(self.statement_calls[-1][2]["headers"]["Authorization"], "Bearer " + token)
            self.assertNotIn("Set-Cookie", response.headers)
        self.assertEqual(self.refresh_calls, ["private-refresh-token", "rotated-refresh-token"])

    def test_refresh_rejection_revokes_the_session(self):
        self.expires_in = 10
        self.login()
        self.refresh_responses.append(http_response(400, {"error": "invalid_grant"}))
        self.assertEqual(self.browser("GET", "/api/session").status_code, 401)
        self.assertEqual(self.browser("GET", "/").location, "/login")
        self.assertFalse(self.statement_calls)


if __name__ == "__main__":
    unittest.main()
