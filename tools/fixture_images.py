"""Approved, immutable Linux/amd64 tools used only by isolated certificate fixtures."""

CERTBOT_IMAGE = (
    'certbot/certbot:v5.8.0@sha256:'
    '398c47284a6d6782825be71685f677ef3a1e65b8b5c278a8b1e99f6da84b4eb9'
)
PEBBLE_IMAGE = (
    'ghcr.io/letsencrypt/pebble:2.10.1@sha256:'
    '5080492187bea12909c053d7a2b90a9e95104ef1c82381828e7823db71ce7e54'
)
CHALLENGE_IMAGE = (
    'ghcr.io/letsencrypt/pebble-challtestsrv:2.10.1@sha256:'
    '1994a326ec60e0de5e8051aa192269c1aaeff0969a4b703d11fa51370f3dc1a8'
)
ACME_IMAGES = (CERTBOT_IMAGE, PEBBLE_IMAGE, CHALLENGE_IMAGE)
