"""Shared authenticated archive operations, also usable by root recovery workers."""

import os as os

from shared.archive import decrypt_archive as decrypt_archive
from shared.archive import encrypt_archive as encrypt_archive
from shared.archive import extract_archive as extract_archive
