# Build from the repository root:
#   docker build -f examples/gateway-demo/Dockerfile -t gateway-demo:local .
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /src
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir ".[all]"

WORKDIR /app
COPY examples/gateway-demo/ ./

ENV PYTHONPATH=/app \
    GATEWAY_PORT=8000
EXPOSE 8000

CMD ["python", "-m", "gateway_demo"]
