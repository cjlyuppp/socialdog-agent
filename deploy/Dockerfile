FROM python:3.13.11-slim-bookworm@sha256:20080e807bfc404f8450b185cf0fc95d553462673598549613735f70a5b4d5d0 AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
COPY pyproject.toml ./
COPY src/ ./src/
ARG PIP_INDEX_URL=https://pypi.org/simple
RUN python -m pip install --no-cache-dir --index-url "$PIP_INDEX_URL" .
COPY frontend/dist/ ./frontend/dist/
COPY deploy/serve.py ./serve.py
ARG SOURCE_REVISION
LABEL org.opencontainers.image.revision=$SOURCE_REVISION
USER 10001:10001
CMD ["python", "serve.py"]

FROM runtime AS test
USER root
ARG PIP_INDEX_URL=https://pypi.org/simple
RUN python -m pip install --no-cache-dir --index-url "$PIP_INDEX_URL" 'httpx>=0.27,<1'
COPY tests/ ./tests/
COPY docs/openapi.json ./docs/openapi.json
USER 10001:10001
RUN python -m unittest discover -s tests

FROM runtime AS release
