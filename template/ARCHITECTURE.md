# Architecture deep dive

This template enforces 8 rules. Each section below explains: **what
the rule says**, **why it exists**, **how it's enforced**, and
**how to opt out** (when relevant).

---

## Layout in one diagram

```
app/
├── infra/                              ← bottom layer
│   └── db/
│       ├── engine.py                   ← get_engine, get_session_maker
│       └── tables/                     ← ALL ORM tables (rule 6)
│           ├── base.py                 ← Base = DeclarativeBase
│           └── <entity>.py             ← one file per table
│
├── modules/                            ← business logic, vertically sliced
│   └── <domain>/
│       ├── __init__.py                 ← public surface (rule 4)
│       ├── models/                     ← domain entities, no infra
│       ├── ports/                      ← Protocol interfaces
│       │   ├── <name>_uow.py           ← UoW Protocol — `_uow` suffix REQUIRED
│       │   └── <repo_name>.py          ← Repository Protocols
│       ├── adapters/                   ← concrete impls
│       │   ├── <name>_uow/sqla.py      ← UoW impl — only place sessions live
│       │   └── <repo_name>/sqla.py     ← Repository impls
│       └── services/                   ← use cases, takes UoW Protocol
│
└── entrypoints/                        ← composition root
    ├── cli/                            ← Typer commands
    └── api/                            ← FastAPI routes
```

The dependency direction is strictly downward:

```
entrypoints  →  modules  →  infra
```

Never sideways into another module's internals, never upward from infra
to modules.

---

## Rule 1 — services/models/ports must NOT import infra

**What:** Inside `app/modules/*/services|models|ports/*`, banned imports:

- `app.infra.*`
- `sqlalchemy*`, `asyncpg`, `psycopg*`
- `httpx`, `requests`, `aiohttp`
- `playwright*`, `selenium*`
- `boto*`

`loguru` and stdlib are fine.

**Why:** the application layer should be unit-testable without spinning
up a real DB or HTTP server. If a service imports `sqlalchemy`, every
test of that service drags the ORM in. Worse, you've coupled "what the
business does" to "how we happen to persist it" — making both harder
to change.

**How:** `import-linter` `forbidden` contract in `.importlinter`. Set
`allow_indirect_imports = True` so transitive chains through a module's
own `__init__.py` (which lazy-loads infra-bound factories) aren't false
positives.

**Opt-out:** none. If you find yourself wanting infra in a service,
you're either:
- doing infra work that belongs in an adapter, OR
- writing an entrypoint disguised as a service

---

## Rule 2 — PEP/style/types

**What:** `ruff check` (with the configured rule set) + `ruff format`
+ `mypy --strict app/`.

**Why:** baseline. Keeps the codebase scannable and catches type
mismatches before runtime.

**How:** `ruff` and `mypy` hooks in `.pre-commit-config.yaml`.

**Opt-out:** per-file ignores in `pyproject.toml`'s `[tool.ruff.lint.per-file-ignores]`.

---

## Rule 3 — Adapter folder = port file stem, class inherits port Protocol

**What:** every adapter implementation must live at:

```
app/modules/<m>/adapters/<port_stem>/<impl>.py
```

where `<port_stem>` matches a file in `app/modules/<m>/ports/`. AND
the public class in the adapter file must inherit (transitively) from
a class declared in the matching port file (or a sibling file in the
same adapter folder, for shared base classes).

**Why:** the "find me the impl of port X" navigation should be
mechanical: open `adapters/X/`. Loose adapter files (e.g.
`adapters/sqla_repo.py`) make the port↔impl mapping invisible until
you grep.

The class-inheritance check catches "right folder but wrong port" —
e.g. a file under `adapters/orders/` defining a class that actually
implements the `Customers` Protocol means the file is in the wrong
adapter folder.

**How:** `scripts/lint_arch.py` `check_rule_3()`. Walks every module's
`adapters/` folder, verifies each subfolder pairs with a port file,
and checks every defined class inherits from a Protocol declared in
that port file.

**Opt-out:** classes with no base class at all (pure DTOs / utility
types) are exempt — only inheriting classes are checked.

---

## Rule 4 — Cross-module imports via `__init__.py` only

**What:** code anywhere in `app/modules/<a>/` may import
`app.modules.<b>` (the package itself), but NOT
`app.modules.<b>.adapters.X`, `app.modules.<b>.models.X`,
`app.modules.<b>.ports.X`, or `app.modules.<b>.services.X`.

The neighbour module's `__init__.py` IS the contract.

**Why:** internal refactors stay internal. Today's
`app.modules.billing.adapters.invoice_repo.SqlaInvoiceRepository` can
become tomorrow's
`app.modules.billing.adapters.invoice_journal.SqlaInvoiceJournalRepository`
without breaking 17 other modules — IF nobody outside billing reaches
in directly.

**How:** `scripts/lint_arch.py` `check_rule_4()`. AST-walks every
file under `app/modules/`, flags any `from app.modules.<other>.{adapters,services,models,ports}`
import. `app/entrypoints/` is exempt (composition root).

**Opt-out:** none for application code. If you genuinely need a
neighbour's internals, expose them via that neighbour's `__init__.py`.

---

## Rule 5 — `infra/` never imports from `modules/` or `entrypoints/`

**What:** files under `app/infra/` cannot import from `app.modules` or
`app.entrypoints`.

**Why:** lower layer must not depend on higher layer. Otherwise you
get cycles: infra needs a module to function, module imports infra,
boom.

**How:** `import-linter` `forbidden` contract.

**Opt-out:** none. If infra needs to know about a domain concept,
that concept probably belongs IN infra.

---

## Rule 6 — ORM tables ONLY in `app/infra/db/tables/`

**What:** no `__tablename__` assignments and no `Base` subclasses
outside `app/infra/db/tables/`.

**Why:** `Base.metadata` becomes a mess if tables are scattered across
modules — Alembic autogenerate misses some, drops others, and
migrations stop being trustworthy. Schema is shared infrastructure;
keep it in one folder.

**How:** `scripts/lint_arch.py` `check_rule_6()`. AST-walks every
.py file in `app/`, flags `__tablename__` assignments and `class Foo(Base):`
declarations outside the allow-listed folder.

**Opt-out:** none. The schema is the contract between adapter
impls and the DB; centralizing it is non-negotiable.

---

## Rule 7 — Sessions/commits ONLY in `*_uow/` adapter files

**What:** these primitives may appear ONLY in files matching
`app/modules/*/adapters/*_uow/*.py`:

- `get_session_maker()`
- `Session()` / `AsyncSession(...)` direct construction
- `session.commit()`, `session.rollback()`, `session.close()`
- `_session.commit()`, `_session.rollback()`, `_session.close()`
- `transaction()` from `app.infra.db`

Repository adapters MUST take a session in `__init__` and use it
directly. They MUST NOT create sessions, commit, or roll back on
their own.

**Why:** the UoW is the ONLY place where transactions begin and end.
A repository that calls `session.commit()` mid-method bypasses the
service-level "explicit commit at the end" contract. A service that
forgot to commit gets rollback-on-exit (default behavior of UoW
`__aexit__`) — which is the safe failure mode.

**How:** `scripts/lint_arch.py` `check_rule_7()`. Regex-greps every
.py file under `app/`, matches the patterns above, and fails if
found outside an adapter folder whose name ends with `_uow`.

**Opt-out:** none for application code. `app/infra/db/` itself
defines these primitives so it's whitelisted.

---

## Rule 8 — Max 3 args per function (PLR0913, max-args=3)

**What:** `ruff PLR0913` configured with `max-args=3`. Functions and
methods that exceed 3 positional/keyword args fail the check.

**Why:** at 4+ args, you're probably passing a "thing" — bundle them
into a `@dataclass` or Pydantic model and the call site becomes
self-documenting. `Foo(name=x, address=y, age=z, email=w)` becomes
`Foo(person=Person(...))`.

**How:** `ruff` rule PLR0913 + `[tool.ruff.lint.pylint] max-args = 3`
in `pyproject.toml`.

**Opt-out:** per-file ignores via `[tool.ruff.lint.per-file-ignores]`:

- `tests/*` — fixtures often take many params
- `app/modules/*/models/*.py` — DTOs and dataclasses ARE the bundle
- `app/entrypoints/cli/*.py` — Typer commands' params are the UX
- `app/entrypoints/api/*.py` — FastAPI route handlers same

`__init__` of dataclasses doesn't count (the dataclass IS the bundle).

---

## Cosmic Python UoW pattern in this template

The example module shows the exact shape you should replicate:

```python
# port — pure abstraction
class ExampleUoW(Protocol):
    items: ItemJournalRepository

    async def __aenter__(self) -> ExampleUoW: ...
    async def __aexit__(self, ...) -> None: ...
    async def commit(self) -> None: ...
    async def rollback(self) -> None: ...

# adapter — owns the session
class SqlaExampleUoW(ExampleUoW):
    async def __aenter__(self):
        self._session = get_session_maker()()
        self.items = SqlaItemJournalRepository(self._session)
        return self
    async def __aexit__(self, ...):
        try: await self.rollback()      # default = rollback
        finally: await self._session.close()
    async def commit(self):  await self._session.commit()
    async def rollback(self): await self._session.rollback()

# service — explicit commit, infra-blind
async def create_item(uow: ExampleUoW, name: str) -> Item:
    async with uow:
        item = await uow.items.create(name=name)
        await uow.commit()              # required — exit defaults to rollback
        return item
```

Why explicit commit instead of "commit on success exit":

> If you forget the commit and the function returns normally, NOTHING
> persists. That's almost always what you want. The implicit-commit
> alternative means a forgotten commit silently saves half-baked
> intermediate state.

This is the Cosmic Python book's choice and it has held up well in
practice.
