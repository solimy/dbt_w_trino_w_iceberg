FROM ghcr.io/opentofu/opentofu:1.12.12-minimal AS tofu
FROM openpolicyagent/opa:1.8.0-istio-static AS opa

FROM python:3.12-slim
COPY --from=tofu /usr/local/bin/tofu /usr/local/bin/tofu
COPY --from=opa /opa /usr/local/bin/opa
RUN pip install --no-cache-dir dbt-core==1.10.5 dbt-trino==1.9.3 trino==0.336.0
WORKDIR /repo/tools/data_product_manager
ENV PYTHONDONTWRITEBYTECODE=1
ENTRYPOINT ["python", "-m", "src.platform"]
