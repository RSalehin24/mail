"""In-process relay actions: verify with the local Maddy CLI; Maddy reloads itself on the write."""

from helper.maddy import MaddyAccess, cli_succeeded
from helper.submission import RelayActions


class LocalVerifier:
    def __init__(self, maddy: MaddyAccess) -> None:
        self.maddy = maddy

    def verify(self) -> bool:
        return cli_succeeded(self.maddy.change(("verify-config",)))


def local_relay_actions(maddy: MaddyAccess) -> RelayActions:
    return RelayActions(LocalVerifier(maddy))
