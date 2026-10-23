"""Atomic persistent rolling budgets, reserved before expensive or external work."""

import math
from dataclasses import dataclass

from backend.database import Database
from shared.errors import ServiceError


@dataclass(frozen=True)
class BudgetReservation:
    bucket: str
    peer: str
    now: float


class Budgets:
    def __init__(self, database: Database) -> None:
        self.database = database

    def reserve(self, reservation: BudgetReservation) -> None:
        limits = {"auth": (30, 15), "send": (5, 3)}
        admin_limit, peer_limit = limits[reservation.bucket]
        with self.database.transaction() as connection:
            rows = connection.execute(
                "SELECT peer,reserved_at FROM auth_rate_limits "
                "WHERE bucket=? AND reserved_at>? ORDER BY reserved_at",
                (reservation.bucket, reservation.now - 900),
            ).fetchall()
            peer_times = [row[1] for row in rows if row[0] == reservation.peer]
            delays = self._delays([row[1] for row in rows], admin_limit, reservation.now)
            delays += self._delays(peer_times, peer_limit, reservation.now)
            if delays:
                raise ServiceError("rate_limited", 429, max(delays))
            connection.execute(
                "INSERT INTO auth_rate_limits(bucket,peer,reserved_at) VALUES(?,?,?)",
                (reservation.bucket, reservation.peer, reservation.now),
            )

    @staticmethod
    def _delays(times: list[float], limit: int, now: float) -> list[int]:
        if len(times) < limit:
            return []
        return [max(1, math.ceil(times[len(times) - limit] + 900 - now))]
