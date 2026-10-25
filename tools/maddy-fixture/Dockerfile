FROM foxcpp/maddy:0.9.5@sha256:2b0c7e0e06b5e6e9e0738a2d6a826dc03b945e864db69288efd7fad4a62ee1e8
COPY maddy /bin/maddy
ARG FIXTURE_INPUTS_SHA256
LABEL mail-admin.scope="isolated-postmaster-fixture" \
      mail-admin.fixture-inputs-sha256="${FIXTURE_INPUTS_SHA256}" \
      org.opencontainers.image.revision="58e8a11423e140ad37063ce8dfc446de4c1591ed" \
      org.opencontainers.image.version="0.9.5+postmaster-fixture.1"
