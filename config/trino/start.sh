#!/bin/sh
set -eu

CA_DIR="/caddy_data/caddy/pki/authorities/local"
ROOT="$CA_DIR/root.crt"
INT="$CA_DIR/intermediate.crt"

TS="/tmp/caddy-truststore.jks"
TSPASS="changeit"

echo "[trino-start] waiting for Caddy CA ..."
i=0
while [ ! -f "$ROOT" ] || [ ! -f "$INT" ]; do
  i=$((i+1))
  if [ $i -gt 90 ]; then
    echo "[trino-start] ERROR: CA files not found after 90s: $ROOT / $INT" >&2
    ls -l "$CA_DIR" || true
    exit 1
  fi
  sleep 1
done
echo "[trino-start] CA present."

# Create/refresh a private JVM truststore with BOTH root and intermediate.
# Import is idempotent: duplicate alias errors are ignored.
create_or_update_ts() {
  local cert="$1" alias="$2"
  keytool -importcert -trustcacerts -noprompt \
    -alias "$alias" \
    -file "$cert" \
    -keystore "$TS" \
    -storepass "$TSPASS" >/dev/null 2>&1 || true
}

# Ensure the truststore exists first (so imports work reliably)
[ -f "$TS" ] || keytool -genkeypair -alias _bootstrap -dname CN=tmp -keystore "$TS" -storepass "$TSPASS" -keypass "$TSPASS" -storetype JKS >/dev/null 2>&1 || true
# Remove the bootstrap entry if present
keytool -delete -alias _bootstrap -keystore "$TS" -storepass "$TSPASS" >/dev/null 2>&1 || true

create_or_update_ts "$ROOT" caddy-local-root
create_or_update_ts "$INT"  caddy-local-intermediate

# Point Trino's JVM at the truststore
export JAVA_TOOL_OPTIONS="${JAVA_TOOL_OPTIONS:-} -Djavax.net.ssl.trustStore=$TS -Djavax.net.ssl.trustStorePassword=$TSPASS -Djavax.net.ssl.trustStoreType=JKS"

echo "[trino-start] JAVA_TOOL_OPTIONS=$JAVA_TOOL_OPTIONS"
echo "[trino-start] launching Trino..."
exec /usr/lib/trino/bin/run-trino
