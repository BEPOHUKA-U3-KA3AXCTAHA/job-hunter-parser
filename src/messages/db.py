"""SQLAlchemy ORM models + async engine — TRANSITIONAL SHIM.

History: this file used to define ALL ORM tables for the project. The
modular refactor (classfieds-style) moved them per-module:

- CompanyRow, JobPostingRow → app.modules.companies.adapters.orm
- DecisionMakerRow          → app.modules.people.adapters.orm   (PENDING)
- ApplyRow                  → app.modules.applies.adapters.orm  (PENDING)

For now this file:
1. RE-EXPORTS the moved classes from their new location
2. Keeps the not-yet-moved tables defined here
3. Keeps engine + session helpers (will move to app/infra/db when refactor completes)

Existing imports `from src.messages.db import X` continue to work via the
re-exports below until callers are updated.
"""
from __future__ import annotations

import os
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import JSON, ForeignKey, String, UniqueConstraint
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Mapped, mapped_column, relationship

# Single shared declarative Base lives in app/infra/db now
from app.infra.db import Base
# Re-export ORM classes that already moved to modules
from app.modules.companies.adapters.orm import CompanyRow, JobPostingRow  # noqa: F401


class DecisionMakerRow(Base):
    __tablename__ = "decision_makers"
    __table_args__ = (UniqueConstraint("company_id", "full_name", name="uq_dm_company_name"),)

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    company_id: Mapped[UUID] = mapped_column(ForeignKey("companies.id"), index=True)

    full_name: Mapped[str] = mapped_column(String(200))
    role: Mapped[str] = mapped_column(String(50))
    title_raw: Mapped[str | None] = mapped_column(String(300))
    location: Mapped[str | None] = mapped_column(String(200))
    source: Mapped[str | None] = mapped_column(String(50))

    # All contact channels in one JSON field. Easily extensible without schema migrations.
    # Example: {"email": "x@y.com", "linkedin": "https://...", "twitter": "@handle",
    #           "github": "ghuser", "website": "https://...", "phone": "+1..."}
    contacts: Mapped[dict] = mapped_column(JSON, default=dict)

    # No dates on dm — freshness is tracked at company level via last_dm_scan_at.
    company: Mapped[CompanyRow] = relationship(back_populates="decision_makers")
    applies: Mapped[list[ApplyRow]] = relationship(back_populates="decision_maker")


class ApplyRow(Base):
    """One outreach attempt per (job_posting, decision_maker, attempt_no).

    Two flanks share this table:
      - flank='dm_outreach' — direct contact to a specific person (CEO/CTO via LinkedIn/email/telegram)
      - flank='mass_apply'  — submission to a posting's apply form (Easy Apply / Workday / Greenhouse / careers@)

    For mass_apply, decision_maker_id points to a SYNTHETIC "Hiring Team" DM
    (role=HR, contacts={email: careers@...}) — keeps the (job, dm) tuple uniform.
    """
    __tablename__ = "applies"
    __table_args__ = (
        UniqueConstraint(
            "job_posting_id", "decision_maker_id", "attempt_no",
            name="uq_apply_job_dm_attempt",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    job_posting_id: Mapped[UUID | None] = mapped_column(ForeignKey("job_postings.id"), index=True)
    decision_maker_id: Mapped[UUID] = mapped_column(ForeignKey("decision_makers.id"), index=True)
    attempt_no: Mapped[int] = mapped_column(default=1)

    # Origin & routing
    flank: Mapped[str] = mapped_column(String(20), default="dm_outreach")   # mass_apply | dm_outreach
    method: Mapped[str] = mapped_column(String(20), default="manual")
    # method: manual | hand_written | auto_apply | auto_outreach | semi_auto
    channel: Mapped[str | None] = mapped_column(String(30))
    # channel: linkedin | linkedin_inmail | email | telegram | ats_easy_apply |
    #          ats_workday | ats_greenhouse | ats_lever | ats_ashby | other

    # Scoring
    relevance_score: Mapped[int] = mapped_column(default=0)

    # Status lifecycle:
    # new → generated → queued → sent → seen → replied → interview_scheduled →
    # interviewing → offer/accepted/rejected/no_reply/failed
    status: Mapped[str] = mapped_column(String(30), default="new")

    # Content
    subject: Mapped[str | None] = mapped_column(String(500))      # email/inmail Subject:
    body: Mapped[str | None] = mapped_column(String(4000))         # DM body or email body
    cover_letter: Mapped[str | None] = mapped_column(String(4000))  # ATS form CL field
    form_responses: Mapped[dict | None] = mapped_column(JSON)       # {field_name: answer} for ATS forms
    apply_url: Mapped[str | None] = mapped_column(String(500))      # exact URL we submitted to

    notes: Mapped[str] = mapped_column(String(2000), default="")

    # Dates
    generated_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    sent_at: Mapped[datetime | None]                               # when actually delivered
    response_at: Mapped[datetime | None]                           # when first reply seen

    decision_maker: Mapped[DecisionMakerRow] = relationship(back_populates="applies")


# --- Engine & session factory ---

def _get_database_url() -> str:
    url = os.environ.get("DATABASE_URL", "").strip()
    if url:
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql+asyncpg://", 1)
        elif url.startswith("postgresql://") and "+asyncpg" not in url:
            url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
        elif url.startswith("sqlite:///") and "+aiosqlite" not in url:
            url = url.replace("sqlite:///", "sqlite+aiosqlite:///", 1)
        return url
    return "sqlite+aiosqlite:///jhp.db"


_engine = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def get_engine():
    global _engine
    if _engine is None:
        _engine = create_async_engine(_get_database_url(), echo=False)
    return _engine


def get_session_maker() -> async_sessionmaker[AsyncSession]:
    global _sessionmaker
    if _sessionmaker is None:
        _sessionmaker = async_sessionmaker(get_engine(), expire_on_commit=False)
    return _sessionmaker


async def init_db() -> None:
    """Create tables if missing, then auto-migrate columns + unique constraints.

    Order:
      1. _drop_renamed_tables — drop tables that were renamed in the model (one-shot migrations)
      2. _drop_stale_unique_tables — drop tables whose unique constraints diverged
      3. create_all — create anything missing
      4. _sync_columns — ADD/DROP columns to match the model

    Caveat: drops wipe data in those tables — only safe for tables we know are
    transient (applies — outreach attempts, regenerated from curated set anyway).
    """
    async with get_engine().begin() as conn:
        await conn.run_sync(_drop_renamed_tables)
        await conn.run_sync(_drop_stale_unique_tables)
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_sync_columns)


def _drop_renamed_tables(sync_conn) -> None:
    """One-shot drops for tables that were renamed in the model.

    Add an entry here when you rename a table — the OLD table stays in the DB
    forever otherwise. Each entry: (old_name, new_name). We drop OLD only if
    NEW already exists in the model (means rename happened) AND OLD still exists
    in the DB.
    """
    from loguru import logger
    from sqlalchemy import inspect, text

    RENAMES = [("messages", "applies")]
    inspector = inspect(sync_conn)
    model_tables = set(Base.metadata.tables.keys())

    for old, new in RENAMES:
        if new not in model_tables:
            continue
        if not inspector.has_table(old):
            continue
        row_count = sync_conn.execute(text(f"SELECT COUNT(*) FROM {old}")).scalar()
        logger.warning(
            "Auto-migration: renamed {} → {} in model. Dropping old table ({} rows).",
            old, new, row_count,
        )
        sync_conn.execute(text(f"DROP TABLE {old}"))


def _drop_stale_unique_tables(sync_conn) -> None:
    """If the live unique constraints don't match the model, drop the table.

    SQLite can't ALTER constraints, and recreating with new uniques while keeping
    data is hairy. We only do this for tables that are safe to wipe:
    currently `messages` (outreach attempts get regenerated anyway).
    """
    from loguru import logger
    from sqlalchemy import inspect, text

    SAFE_TO_WIPE = {"messages", "applies"}
    inspector = inspect(sync_conn)

    for table_name, table in Base.metadata.tables.items():
        if table_name not in SAFE_TO_WIPE or not inspector.has_table(table_name):
            continue

        live_uniques = {
            tuple(sorted(u["column_names"]))
            for u in inspector.get_unique_constraints(table_name)
        }
        model_uniques = {
            tuple(sorted(c.name for c in constraint.columns))
            for constraint in table.constraints
            if constraint.__class__.__name__ == "UniqueConstraint"
        }
        if live_uniques != model_uniques:
            row_count = sync_conn.execute(text(f"SELECT COUNT(*) FROM {table_name}")).scalar()
            logger.warning(
                "Auto-migration: stale unique on {} (live={}, model={}). Dropping ({} rows).",
                table_name, live_uniques, model_uniques, row_count,
            )
            sync_conn.execute(text(f"DROP TABLE {table_name}"))


def _sync_columns(sync_conn) -> None:
    """Diff model schema vs live DB schema. Add missing columns, drop extra ones.

    Uses sqlalchemy.inspect for the comparison.
    SQLite 3.35+ supports both ADD COLUMN and DROP COLUMN without data loss.
    """
    from loguru import logger
    from sqlalchemy import inspect, text

    inspector = inspect(sync_conn)

    for table_name, table in Base.metadata.tables.items():
        if not inspector.has_table(table_name):
            continue  # create_all() already built it

        model_cols = {col.name: col for col in table.columns}
        existing_cols = {c["name"] for c in inspector.get_columns(table_name)}

        # 1. ADD COLUMN for new fields in the model
        for name, col in model_cols.items():
            if name in existing_cols:
                continue

            col_type = col.type.compile(dialect=sync_conn.dialect)
            nullable = "" if col.nullable else " NOT NULL DEFAULT ''"
            default = ""
            if col.default is not None and col.default.is_scalar:
                val = col.default.arg
                if isinstance(val, str):
                    default = f" DEFAULT '{val}'"
                elif isinstance(val, (int, float, bool)):
                    default = f" DEFAULT {int(val) if isinstance(val, bool) else val}"

            stmt = f'ALTER TABLE {table_name} ADD COLUMN "{name}" {col_type}{default}{nullable}'
            try:
                sync_conn.execute(text(stmt))
                logger.info("Auto-migration: added {}.{} ({})", table_name, name, col_type)
            except Exception as e:
                logger.warning("Auto-migration ADD failed for {}.{}: {}", table_name, name, e)

        # 2. DROP COLUMN for columns that are no longer in the model.
        # SQLite refuses to drop a column if it's part of an index or PK -
        # those will fail safely with a warning and be left in place.
        for name in existing_cols - set(model_cols.keys()):
            stmt = f'ALTER TABLE {table_name} DROP COLUMN "{name}"'
            try:
                sync_conn.execute(text(stmt))
                logger.info("Auto-migration: dropped {}.{}", table_name, name)
            except Exception as e:
                logger.warning("Auto-migration DROP failed for {}.{}: {}", table_name, name, e)


def describe_db() -> str:
    url = _get_database_url()
    if url.startswith("sqlite"):
        return f"SQLite file: {url.split('///')[-1]}"
    return url.split("@")[-1] if "@" in url else url
