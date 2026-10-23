"""Start the real non-root web image from restored files without fresh provisioning."""

from types import SimpleNamespace

from backend.database import Database
from backend.secrets import SecretKeys
from shared.files import protected_json
from tests.runtime_support import RuntimeFixture


class RestoredRuntimeFixture(RuntimeFixture):
    def __init__(self, restored):
        super().__init__()
        self.restored = restored
        marker = restored / "operator/installation.json"
        installation = protected_json(marker)
        self.database = Database(restored / "dashboard/admin_data.db",
                                 installation["installation_id"])
        self.keys = SecretKeys.load(restored / "operator/secrets", marker)
        self.tls = restored / "tls"
        self.certificates = SimpleNamespace(trust=self.tls / "fullchain.pem")

    def bind_sources(self):
        return (
            (self.restored / "dashboard", "/app/data"),
            (self.restored / "operator/secrets", "/run/secrets/mail-admin"),
            (self.restored / "operator/installation.json", "/run/mail-admin-installation.json"),
            (self.directories["socket"], "/run/mail-admin-helper"),
        )
