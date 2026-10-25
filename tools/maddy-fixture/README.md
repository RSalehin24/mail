# Isolated postmaster fixture

The user approved this Maddy 0.9.5 correction and the hash-pinned source/compiler
downloads. Production keeps its separately selected Maddy image.

`postmaster.patch` changes only SMTP forward-path parsing. A complete,
case-insensitive bare `postmaster` token is accepted; MAIL FROM parsing and ordinary
mailbox parsing keep their upstream behavior. `postmaster_test.go` covers the parser.
`tests/maddy/test_operational_recipients.py` verifies actual SMTP-to-IMAP delivery,
ordinary recipients, malformed lookalikes and unchanged ESMTP rejection.

`inputs.json` records the source, patch, Go 1.23.5, Zig 0.14.1, original `go.sum`,
runtime-base and resulting executable hashes. The approved upstream archives are:

- `https://codeload.github.com/foxcpp/maddy/tar.gz/58e8a11423e140ad37063ce8dfc446de4c1591ed`
- `https://codeload.github.com/emersion/go-smtp/tar.gz/c8519ae4fb23`
- `https://go.dev/dl/go1.23.5.darwin-arm64.tar.gz`
- `https://ziglang.org/download/0.14.1/zig-aarch64-macos-0.14.1.tar.xz`

The existing build used `go mod edit -replace` to select the patched local SMTP
source, `go mod download`/`go mod verify`, CGO-enabled Linux/amd64 compilation with
Zig's `x86_64-linux-musl` target, the `docker netgo osusergo` tags, static linking
and version `0.9.5+postmaster-fixture.1`. SQLite support remains enabled. Its full
module/build information, parser-test logs, binary, download receipts and archives
remain in `build/maddy-postmaster-approved/`. Expanded compilers and caches can be
recreated from those verified archives. Go's recorded VCS metadata identifies the
containing workspace; source provenance is established by the pinned source and
patch hashes. A rebuild must retain its own executable/image receipt.

The Dockerfile replaces `/bin/maddy` in the pinned original runtime base. The
approved current executable SHA-256 is
`e4596fed2f1f58ad7dda3bebeece94f806a5e56cc3bba6f2c0bc37ce0acf2dd6`.
`tools/maddy_fixture.py` records its OCI index and configuration digests. Containerd
and Docker's classic image store expose those different identities for the same
verified image; fixtures accept only these pinned identities on Linux/amd64.
