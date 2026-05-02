"""Unified ORM schema. Importing this module registers every table with
`Base.metadata` — Alembic env.py and any code that needs the metadata can
just `from app.infra.db.orm import *` (or import specific Row classes).
"""
from app.infra.db.orm.applies import ApplyRow
from app.infra.db.orm.companies import CompanyRow, JobPostingRow
from app.infra.db.orm.people import DecisionMakerRow

__all__ = ["ApplyRow", "CompanyRow", "DecisionMakerRow", "JobPostingRow"]
