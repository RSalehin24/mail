"""Reviewed disposable guest inputs, independent of production resource selection."""

BASE_URL = (
    "https://cloud.debian.org/images/cloud/bookworm/20261021-2638/"
    "debian-12-genericcloud-amd64-20261021-2638.qcow2"
)
BASE_SHA512 = (
    "9f181877006e8d9ca4f4554eba8729a829936f0f7d1db5e77f69b1ef0f6bc372b"
    "4d46f3dae39b10d84f9131496c2cc1d449264f26577fe934a79de5a5512a3dd"
)
GUEST_MEMORY_MIB = 256
CONTAINER_MEMORY_MIB = 384
HOST_RESERVE_MIB = 128
GUEST_SWAP_MIB = 256
LOCAL_GUEST_MEMORY_MIB = 1024
LOCAL_CONTAINER_MEMORY_MIB = 1280
LOCAL_DISK_RESERVE_BYTES = 3 * 1024**3
MOCK_DOCKER_STARTUP_SECONDS = 180
