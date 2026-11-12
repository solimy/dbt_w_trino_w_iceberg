import math
import os
import re
from dataclasses import dataclass, field
from secrets import token_urlsafe
from threading import Lock
from time import monotonic, time
from urllib.parse import urlsplit

import requests
from authlib.integrations.base_client import OAuthError
from authlib.integrations.flask_client import OAuth
from flask import Flask, jsonify, redirect, request, send_from_directory, session
from joserfc.errors import JoseError


LOGIN_TTL = 300
SESSION_TTL = 8 * 3600
REFRESH_WINDOW = 15
STATEMENT_PATH = re.compile(
    r"/v1/statement/(?:queued|executing|(?P<partial>executing/partialCancel))"
    r"/(?P<id>[a-zA-Z0-9_-]+)(?(partial)/[0-9]+)"
    r"/[a-zA-Z0-9_-]+/[0-9]+"
)
REQUEST_HEADERS = (
    "X-Trino-Catalog",
    "X-Trino-Schema",
)
RESPONSE_HEADERS = (
    "X-Trino-Set-Catalog",
    "X-Trino-Set-Schema",
    "Retry-After",
)


class ExpiringStore:
    """Thread-safe storage shared by requests in a single Gunicorn worker."""

    def __init__(self, max_ttl):
        self.max_ttl = max_ttl
        self.entries = {}
        self.lock = Lock()

    def set(self, key, value, timeout):
        now = monotonic()
        with self.lock:
            for expired in [key for key, (_, expiry) in self.entries.items() if expiry <= now]:
                del self.entries[expired]
            self.entries[key] = (value, now + min(timeout, self.max_ttl))

    def get(self, key):
        with self.lock:
            value, expiry = self.entries.get(key, (None, 0))
            if expiry > monotonic():
                return value
            self.entries.pop(key, None)
            return None

    def delete(self, key):
        with self.lock:
            self.entries.pop(key, None)


class SessionExpired(Exception):
    pass


def access_credentials(token):
    access_token = token["access_token"]
    expires_at = float(token["expires_at"])
    if (
        not re.fullmatch(r"[A-Za-z0-9._~+/-]+=*", access_token)
        or not math.isfinite(expires_at)
        or expires_at <= time()
    ):
        raise ValueError("Invalid access token or expiration")
    return access_token, expires_at


@dataclass
class Identity:
    username: str
    access_token: str
    expires_at: float
    refresh_token: str | None
    queries: set[str] = field(default_factory=set)
    lock: Lock = field(default_factory=Lock)

    def bearer(self, keycloak):
        with self.lock:
            remaining = self.expires_at - time()
            if remaining > REFRESH_WINDOW or (remaining > 0 and not self.refresh_token):
                return self.access_token
            if not self.refresh_token:
                raise SessionExpired
            try:
                token = keycloak.fetch_access_token(
                    grant_type="refresh_token",
                    refresh_token=self.refresh_token,
                    allow_redirects=False,
                )
            except OAuthError as exc:
                if exc.error == "invalid_grant":
                    self.refresh_token = None
                    self.expires_at = 0
                    raise SessionExpired from exc
                raise
            self.access_token, self.expires_at = access_credentials(token)
            self.refresh_token = token.get("refresh_token", self.refresh_token)
            return self.access_token

    def remember(self, query_id):
        with self.lock:
            self.queries.add(query_id)

    def owns(self, query_id):
        with self.lock:
            return query_id in self.queries


def https_url(variable, *, allow_path=False):
    value = os.environ[variable].rstrip("/")
    parts = urlsplit(value)
    if (
        parts.scheme != "https"
        or not parts.hostname
        or parts.username is not None
        or parts.query
        or parts.fragment
        or (parts.path and not allow_path)
    ):
        raise ValueError(f"{variable} must be an HTTPS URL with the expected path")
    return value


def rewrite_statement_uri(uri, upstream, public, query_id, *, partial):
    if not isinstance(uri, str):
        raise ValueError("Invalid statement URI")
    parts = urlsplit(uri)
    path = STATEMENT_PATH.fullmatch(parts.path)
    if (
        f"{parts.scheme}://{parts.netloc}" != upstream
        or parts.fragment
        or path is None
        or path["id"] != query_id
        or bool(path["partial"]) != partial
    ):
        raise ValueError("Unexpected statement URI")
    query = f"?{parts.query}" if parts.query else ""
    return public + parts.path + query


def create_app(*, static_folder="/app/static"):
    public_url = https_url("CONSOLE_PUBLIC_URL")
    issuer = https_url("CONSOLE_ISSUER", allow_path=True)
    trino_url = https_url("CONSOLE_TRINO_URL")
    ca_bundle = os.environ["REQUESTS_CA_BUNDLE"]
    app = Flask(__name__, static_folder=None)
    app.config.update(
        SECRET_KEY=token_urlsafe(32),
        SESSION_COOKIE_NAME="__Host-query-console",
        SESSION_COOKIE_SECURE=True,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        TRUSTED_HOSTS=[urlsplit(public_url).hostname],
        MAX_CONTENT_LENGTH=2 * 1024 * 1024,
    )

    identities = ExpiringStore(max_ttl=SESSION_TTL)
    oauth = OAuth(app, cache=ExpiringStore(max_ttl=LOGIN_TTL))
    keycloak = oauth.register(
        "keycloak",
        client_id="query-console",
        client_secret=os.environ["CONSOLE_CLIENT_SECRET"],
        server_metadata_url=issuer + "/.well-known/openid-configuration",
        client_kwargs={
            "scope": "openid profile",
            "code_challenge_method": "S256",
            "token_endpoint_auth_method": "client_secret_basic",
            "default_timeout": 10,
            "verify": ca_bundle,
        },
    )

    def identity():
        return identities.get(session.get("sid"))

    def end_session():
        identities.delete(session.get("sid"))
        session.clear()

    def error(message, status):
        return jsonify(error=message), status

    @app.errorhandler(SessionExpired)
    def expired_session(_exception):
        end_session()
        return error("Session expired. Sign in to continue.", 401)

    @app.after_request
    def protect_response(response):
        if not request.path.startswith("/assets/"):
            response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        return response

    @app.get("/")
    def index():
        if identity() is None:
            return redirect("/login")
        return send_from_directory(static_folder, "index.html")

    @app.get("/assets/<path:asset>")
    def assets(asset):
        return send_from_directory(static_folder + "/assets", asset)

    @app.get("/login")
    def login():
        if identity() is not None:
            return redirect("/")
        end_session()
        try:
            if keycloak.load_server_metadata().get("issuer") != issuer:
                return error("Unexpected sign-in provider.", 502)
            state = token_urlsafe(32)
            session["login_state"] = state
            return keycloak.authorize_redirect(public_url + "/oauth/callback", state=state)
        except (requests.RequestException, OAuthError, ValueError, RuntimeError):
            return error("The sign-in provider is unavailable.", 502)

    @app.get("/oauth/callback")
    def callback():
        state = request.args.get("state")
        if (
            not state
            or state != session.get("login_state")
            or not request.args.get("code")
            or request.args.get("iss", issuer) != issuer
        ):
            return error("Sign-in failed. Open /login to try again.", 400)
        session.pop("login_state")
        try:
            token = keycloak.authorize_access_token(
                claims_options={"iss": {"essential": True, "values": [issuer]}},
                allow_redirects=False,
            )
            username = token["userinfo"]["preferred_username"]
            if not re.fullmatch(r"[!-~]+", username):
                raise ValueError("Invalid username")
            access_token, expires_at = access_credentials(token)
        except requests.RequestException:
            return error("The sign-in provider is unavailable.", 502)
        except (OAuthError, JoseError, ValueError, TypeError, KeyError):
            return error("Sign-in failed. Open /login to try again.", 400)
        end_session()
        sid = token_urlsafe(32)
        identities.set(
            sid, Identity(username, access_token, expires_at, token.get("refresh_token")), SESSION_TTL
        )
        session["sid"] = sid
        return redirect("/")

    @app.get("/api/session")
    def session_info():
        signed_in = identity()
        if signed_in is None:
            return error("Sign in to continue.", 401)
        try:
            signed_in.bearer(keycloak)
        except (requests.RequestException, OAuthError, ValueError, TypeError, KeyError):
            return error("The sign-in provider could not refresh the session.", 502)
        return jsonify(user=signed_in.username)

    @app.post("/logout")
    def logout():
        if request.headers.get("Origin") != public_url:
            return error("Cross-origin request rejected.", 403)
        end_session()
        return "", 204

    @app.get("/logged-out")
    def logged_out():
        return '<!doctype html><title>Signed out</title><p>Signed out. <a href="/login">Sign in</a></p>'

    @app.post("/v1/statement")
    @app.route("/v1/statement/<path:target>", methods=["GET", "DELETE"])
    def statement(target=None):
        signed_in = identity()
        if signed_in is None:
            return error("Sign in to continue.", 401)
        method = request.method
        if method in {"POST", "DELETE"} and request.headers.get("Origin") != public_url:
            return error("Cross-origin request rejected.", 403)

        path = "/v1/statement"
        query_id = None
        query = ""
        if target is not None:
            path += "/" + target
            match = STATEMENT_PATH.fullmatch(path)
            if match is None or (match["partial"] and method != "DELETE"):
                return error("Unknown statement path.", 404)
            query_id = match["id"]
            if not signed_in.owns(query_id):
                return error("Unknown statement.", 404)
            if request.query_string:
                query = "?" + request.query_string.decode("ascii", errors="replace")

        try:
            access_token = signed_in.bearer(keycloak)
        except (requests.RequestException, OAuthError, ValueError, TypeError, KeyError):
            return error("The sign-in provider could not refresh the session.", 502)

        headers = {
            "Authorization": "Bearer " + access_token,
            "X-Trino-User": signed_in.username,
            "X-Trino-Source": "query-console",
            "Accept": "application/json",
        }
        body = None
        if method == "POST":
            body = request.get_data()
            headers["Content-Type"] = "text/plain; charset=utf-8"
            headers.update({name: request.headers[name] for name in REQUEST_HEADERS if name in request.headers})

        try:
            with requests.Session() as trino:
                trino.trust_env = False
                upstream = trino.request(
                    method,
                    trino_url + path + query,
                    headers=headers,
                    data=body,
                    verify=ca_bundle,
                    timeout=(5, 30),
                    allow_redirects=False,
                )
            status = upstream.status_code
            if status == 401:
                end_session()
                return error("Trino authentication expired. Sign in to continue.", 401)
            if 300 <= status < 400:
                return error("Unexpected redirect from Trino.", 502)
            if status >= 400:
                message = "Trino denied the request." if status == 403 else f"Trino returned HTTP {status}."
                response = jsonify(error=message)
                response.status_code = status
            elif method == "DELETE":
                response = app.response_class(status=status)
            else:
                result = upstream.json()
                if not isinstance(result, dict):
                    raise ValueError("Invalid Trino response")
                result_id = result["id"]
                if not isinstance(result_id, str) or (query_id is not None and result_id != query_id):
                    raise ValueError("Invalid query ID")
                for field_name in ("nextUri", "partialCancelUri"):
                    if result.get(field_name) is not None:
                        result[field_name] = rewrite_statement_uri(
                            result[field_name],
                            trino_url,
                            public_url,
                            result_id,
                            partial=field_name == "partialCancelUri",
                        )
                if query_id is None and (result.get("nextUri") or result.get("partialCancelUri")):
                    signed_in.remember(result_id)
                response = jsonify(result)
                response.status_code = status
            for name in RESPONSE_HEADERS:
                if name in upstream.headers:
                    response.headers[name] = upstream.headers[name]
            return response
        except (requests.RequestException, ValueError, KeyError):
            return error("Trino is unavailable or returned an invalid response.", 502)

    return app
