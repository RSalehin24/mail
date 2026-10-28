FROM node:24-bookworm-slim@sha256:2dd9a4f758e173b65fee15388f7706af6fb01ef50cf7a2c0538711d67a6981ae AS frontend
WORKDIR /build
COPY frontend/package*.json ./
RUN npm ci --ignore-scripts
COPY frontend/ ./
RUN npm run build

FROM python:3.11-slim-bookworm@sha256:8fb0b110b022c70cbe09d3ff1fb313654f31f9c814abb0d48180af56d906a199 AS dependencies
WORKDIR /build
COPY requirements.lock ./
RUN --mount=type=bind,source=build/wheels,target=/wheels,ro \
    pip install --no-index --find-links=/wheels --require-hashes --prefix=/install -r requirements.lock

FROM dependencies AS qualification-dependencies
COPY requirements-dev.lock ./
RUN --mount=type=bind,source=build/wheels,target=/wheels,ro \
    pip install --no-index --find-links=/wheels --require-hashes --prefix=/qualification -r requirements-dev.lock

FROM python:3.11-slim-bookworm@sha256:8fb0b110b022c70cbe09d3ff1fb313654f31f9c814abb0d48180af56d906a199 AS runtime
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 BIND_HOST=0.0.0.0
COPY --from=dependencies /install /usr/local
COPY --from=frontend /build/dist /app/static
COPY backend /app/backend
COPY shared /app/shared
RUN python -m compileall --invalidation-mode checked-hash -q \
    /app/backend /app/shared /usr/local/lib/python3.11
USER 10001:10001
EXPOSE 8443
CMD ["python", "-m", "backend"]

FROM docker:29.4.0-cli@sha256:bb21349a52c00b206ad8b5c03fa52023c741c4cf11f269d40d40b9ebaac73d96 AS docker-client

FROM runtime AS qualification
USER 0:0
COPY build/restic/0.19.1/restic-linux_amd64 /usr/local/bin/restic
COPY --from=docker-client /usr/local/bin/docker /usr/bin/docker
COPY --from=docker-client /usr/local/libexec/docker/cli-plugins/docker-compose /usr/local/libexec/docker/cli-plugins/docker-compose
COPY --from=qualification-dependencies /qualification /usr/local
COPY helper /app/helper
COPY host /app/host
COPY tests /app/tests
COPY tools /app/tools
COPY docs/mail-admin-implementation /app/docs/mail-admin-implementation
COPY Dockerfile docker-compose.yml requirements.lock requirements-dev.lock pyproject.toml Makefile /app/
COPY frontend/src /app/frontend/src
COPY frontend/public /app/frontend/public
COPY frontend/package.json frontend/package-lock.json frontend/tsconfig.json frontend/index.html /app/frontend/
CMD ["python", "tools/run_tests.py", "unit"]
