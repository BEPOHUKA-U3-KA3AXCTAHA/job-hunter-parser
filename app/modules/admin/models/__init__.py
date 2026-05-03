"""Admin module — cross-table diagnostic DTOs."""
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass(slots=True)
class DbStatus:
    """Top-level row counts."""
    total_companies: int
    total_dms: int
    total_applies: int
    sent_today: int


@dataclass(slots=True)
class CompanyDump:
    id: UUID
    name: str
    source: str | None
    is_hiring: bool | None
    last_dm_scan_at: datetime | None


@dataclass(slots=True)
class PersonDump:
    dm_id: UUID
    full_name: str
    role: str | None
    company_name: str
    contacts: dict


@dataclass(slots=True)
class JobDump:
    job_id: UUID
    title: str
    company_name: str | None
    posted_at: datetime | None
    first_seen_at: datetime | None
    source: str | None
    source_url: str | None
    applicants_count: int | None


@dataclass(slots=True)
class StaleCompany:
    id: UUID
    name: str
    last_dm_scan_at: datetime | None
