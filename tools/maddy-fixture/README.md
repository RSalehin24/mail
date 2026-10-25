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
These ignored build artifacts are retained on the original workstation and are not
included in this source checkout. Transfer them privately as described in the
[handover](../../verification/ubuntu-handover.md); do not substitute an unpatched
upstream image when the pinned fixture is unavailable.

The Dockerfile replaces `/bin/maddy` in the pinned original runtime base. The
approved current executable SHA-256 is
`e4596fed2f1f58ad7dda3bebeece94f806a5e56cc3bba6f2c0bc37ce0acf2dd6`.
`tools/maddy_fixture.py` records its OCI index and configuration digests. Containerd
and Docker's classic image store expose those different identities for the same
verified image; fixtures accept only these pinned identities on Linux/amd64.

## Linux rebuild

The native Linux continuation rebuilds the same approved source commits and patch
with Go 1.23.5 and Zig 0.14.1 for Linux/amd64. The original Mac receipts above stay
unchanged. `inputs-linux.json` records the Linux compiler archive hashes, original
`go.sum`, build flags and new executable identity. The Zig archive checksum comes
from the [official download index](https://ziglang.org/download/index.json);
Go's Linux archive checksum comes from its official download metadata.

The rebuild runs the complete SMTP parser suite with external static linking,
`go mod download`, `go mod verify`, and the same `docker netgo osusergo` build tags.
It uses `-buildvcs=false -trimpath` and external static linking for a self-contained
Linux executable; the version remains `0.9.5+postmaster-fixture.1` because the
approved code patch is unchanged. Linux source/compiler archives, parser/build
logs and the executable are retained under `build/maddy-postmaster-linux/` and
`build/setup/maddy-linux-build-external-link.log`.

The executable SHA-256 is
`6900b36419e3398df4d427544b60535d10f60ae8504ca45a98bcbd567d8211de`.
Its image index is
`sha256:c332b48a8a4cc614c314561d5769cf2e5d2bd91c66f9790826abd132141f8489`,
and its configuration is
`sha256:c0e7acee7b21b4af2bd84dfb622a22de5eec6f51206f9178bcbe310216196904`.
These are distinct from the original Mac build, not interchangeable evidence
identities. The resolver accepts only the exact recorded index/configuration pairs
from these two builds. Full fixture assertions must execute against the selected
identity before a new candidate can qualify. Production selection remains separate.

Export fixture images without `--platform` to retain their complete OCI identity.
The remote exporter now selects the actual loaded approved fixture and preserves
that full image rather than requiring only the original Mac image.
