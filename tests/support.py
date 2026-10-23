import secrets
import tempfile
from pathlib import Path

from backend.database import Database
from backend.secrets import SecretKeys


class StateFixture:
    def __init__(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.database = Database.provision(self.root / "admin.db", "fixture-installation")
        self.keys = SecretKeys(secrets.token_bytes(32), secrets.token_bytes(32))
        self.now = 1800000000.0

    def close(self):
        self.directory.cleanup()
