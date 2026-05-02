"""Unified ORM schema. Importing this module registers every table with
`Base.metadata` — Alembic env.py and any code that needs the metadata can
just `from app.infra.db.tables import *` (or import specific Row classes).
"""
from app.infra.db.tables.applies import ApplyRow
from app.infra.db.tables.companies import CompanyRow, JobPostingRow
from app.infra.db.tables.form_answers import FormAnswerRow
from app.infra.db.tables.people import DecisionMakerRow

__all__ = ["ApplyRow", "CompanyRow", "DecisionMakerRow", "FormAnswerRow", "JobPostingRow"]
