ARG GUEST_PLATFORM=linux/amd64
ARG MOCK_BASE=python:3.15.2-slim-bookworm@sha256:96599df822d0e1c6bb700014a05c7f3c8cabc6e0f7ff3c4f9f69cee033b4b5d9
FROM --platform=$GUEST_PLATFORM docker:29.4.0-dind@sha256:a6dd5322747a95cd8e3207bd8d415a8fd20ec34e9c00f06dc019cbd912013489 AS engine

FROM $MOCK_BASE
RUN apt-get update && apt-get install -y --no-install-recommends \
    qemu-system-x86 qemu-utils genisoimage \
    && rm -rf /var/lib/apt/lists/*
COPY --from=engine /usr/local/bin/ /opt/engine/
COPY --from=engine /usr/local/libexec/docker/cli-plugins/docker-compose /opt/engine/cli-plugins/docker-compose
RUN mkdir /opt/payload && \
    tar --sort=name --mtime=@0 --owner=0 --group=0 -C /opt/engine -czf /opt/payload/docker-engine.tar.gz . && \
    install -d -m 0700 -o 10001 -g 10001 /fixture
COPY tools/vm/start.py tools/vm/prepare.py tools/vm/mock_profile.py tools/vm/repair.py /opt/
USER 10001:10001
ENTRYPOINT ["python", "/opt/start.py"]
