"""All ORM table definitions live here — and ONLY here (rule 6).

Why: schema is shared infrastructure. A `Base.metadata` scattered
across multiple modules makes migrations fragile and creates hidden
cross-module coupling. Keeping every `__tablename__` in one folder
makes `alembic revision --autogenerate` reliable.

Convention: one file per domain entity, all subclassing the shared
`Base`. Re-export them here so Alembic's env.py discovers them via
a single import.
"""

from app.infra.db.tables.base import Base

# Re-export your tables here so Alembic discovers them, e.g.:
# from app.infra.db.tables.item import ItemRow
#
# __all__ = ["Base", "ItemRow"]

__all__ = ["Base"]
