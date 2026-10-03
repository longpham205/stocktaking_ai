"""Declarative base every module's table classes extend. Alembic diffs against its metadata.

Declarative is used for *declaring* tables with typed columns only. Repositories query with Core
statements on a connection; there is no Session and no relationship().

The naming convention gives every index and constraint a deterministic name, so a migration can
drop or alter one by name instead of whatever the database made up.
"""

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


metadata = Base.metadata
