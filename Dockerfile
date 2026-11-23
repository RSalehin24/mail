FROM node:24-bookworm-slim@sha256:2dd9a4f758e173b65fee15388f7706af6fb01ef50cf7a2c0538711d67a6981ae AS frontend
WORKDIR /build
COPY frontend/package*.json ./
RUN npm ci --ignore-scripts
COPY frontend/ ./
RUN npm run build

FROM python:3.15.2-slim-bookworm@sha256:96599df822d0e1c6bb700014a05c7f3c8cabc6e0f7ff3c4f9f69cee033b4b5d9 AS dependencies
WORKDIR /build
COPY requirements.lock ./
RUN --mount=type=bind,source=build/wheels,target=/wheels,ro \
    pip install --no-index --find-links=/wheels --require-hashes --prefix=/install -r requirements.lock

FROM dependencies AS qualification-dependencies
COPY requirements-dev.lock ./
RUN --mount=type=bind,source=build/wheels,target=/wheels,ro \
    pip install --no-index --find-links=/wheels --require-hashes --prefix=/qualification -r requirements-dev.lock

# The production Maddy image; its Alpine/musl binary runs on Debian beside the musl loader.
FROM ghcr.io/foxcpp/maddy@sha256:e1074e92a452b84d1135ebe8ab831c5f541d33dcbd39e38fab740a53bbf2f97c AS maddy-binary

FROM python:3.15.2-slim-bookworm@sha256:96599df822d0e1c6bb700014a05c7f3c8cabc6e0f7ff3c4f9f69cee033b4b5d9 AS runtime
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 BIND_HOST=0.0.0.0
COPY --from=dependencies /install /usr/local
COPY --from=frontend /build/dist /app/static
COPY backend /app/backend
COPY shared /app/shared
COPY helper /app/helper
# Only the host modules backend.local_host imports (its AST import closure).
COPY host/__init__.py host/certificates.py host/consumers.py host/docker.py host/locks.py \
     host/mailbox_archive.py host/mailbox_store.py host/mailbox_transaction.py host/preflight.py \
     /app/host/
COPY --from=maddy-binary /bin/maddy /usr/local/bin/maddy
COPY --from=maddy-binary /lib/ld-musl-x86_64.so.1 /lib/ld-musl-x86_64.so.1
RUN python -m compileall --invalidation-mode checked-hash -q \
    /app/backend /app/shared /app/helper /app/host /usr/local/lib/python3.15
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
COPY host /app/host
COPY tests /app/tests
COPY tools /app/tools
COPY Dockerfile docker-compose.yml requirements.lock requirements-dev.lock pyproject.toml Makefile /app/
COPY frontend/src /app/frontend/src
COPY frontend/public /app/frontend/public
COPY frontend/package.json frontend/package-lock.json frontend/tsconfig.json frontend/index.html /app/frontend/
CMD ["python", "tools/run_tests.py", "unit"]
