"""Column building blocks every module's tables share, so money, time and ids look the same
everywhere: ids are BIGINT identities, money is BIGINT (VND has no minor unit), instants are
`timestamptz` defaulting to the database's `now()`."""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Identity
from sqlalchemy import text as sql
from sqlalchemy.orm import Mapped, mapped_column

TIMESTAMP = DateTime(timezone=True)
NOW = sql("now()")
MONEY = BigInteger


def id_column() -> Mapped[int]:
    return mapped_column(BigInteger, Identity(), primary_key=True)


def created_at_column() -> Mapped[datetime]:
    return mapped_column(TIMESTAMP, server_default=NOW)
