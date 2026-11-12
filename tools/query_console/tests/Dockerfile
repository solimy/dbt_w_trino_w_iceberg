FROM mcr.microsoft.com/playwright/python:v1.64.1-noble
RUN pip install --no-cache-dir playwright==1.64.1
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/repo
ENTRYPOINT ["python", "/repo/tools/query_console/tests/browser.py"]
