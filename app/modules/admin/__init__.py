"""Admin module — cross-table diagnostic queries + the API server's
record-result write path. Lives separately from the per-aggregate-root
modules (companies/people/applies) because admin queries cut across all
of them; pushing them into any one module would leak its own concerns
into another module's repository.
"""
from app.modules.admin.models import (
    CompanyDump, DbStatus, JobDump, PersonDump, StaleCompany,
)
from app.modules.admin.ports.admin_uow import AdminRepository, AdminUoW


def default_uow() -> AdminUoW:
    """Composition-root helper — production SQLA-backed admin UoW."""
    from app.modules.admin.adapters.admin_uow.sqla import SqlaAdminUoW
    return SqlaAdminUoW()


__all__ = [
    "AdminRepository", "AdminUoW",
    "CompanyDump", "DbStatus", "JobDump", "PersonDump", "StaleCompany",
    "default_uow",
]
