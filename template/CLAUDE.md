# CLAUDE.md — onboarding for a fresh agent

You are working in a project bootstrapped from this template. The
codebase enforces **8 architecture rules** through 3 linters that
run in pre-commit. Violating a rule fails the commit, so you must
internalize them before you touch code.

This file is your bootloader. After reading it, read
[ARCHITECTURE.md](ARCHITECTURE.md) for the long form on each rule.

---

## The mental model in 30 seconds

```
entrypoints/  →  modules/  →  infra/
   (CLI/API)    (business)    (DB engine, tables)
```

Inside each module:

```
ports/      ← Protocols (interfaces). No infra, no impl.
models/     ← Domain DTOs/dataclasses. No infra.
services/   ← Use cases. Take a UoW Protocol. Explicit commit.
adapters/   ← Concrete impls of ports. Folder name = port file stem.
__init__.py ← Public surface. Outside code imports ONLY from here.
```

The hard rules of dependency direction:
- Application code (`services`/`models`/`ports`) does NOT touch `app.infra`,
  `sqlalchemy`, `httpx`, `playwright`, etc. Pure stdlib + `loguru` + ports.
- `infra/` does NOT import from `modules/` or `entrypoints/`.
- One module does NOT import another module's internals (only
  the neighbor's `__init__.py` is the contract).
- Sessions/commits/rollbacks live ONLY in `adapters/*_uow/` files.

---

## The 8 rules — cheat sheet

| # | Rule | Ifyou break it |
|---|------|----------------|
| 1 | services/models/ports must NOT import `app.infra.*` or any DB/HTTP/automation lib | `import-linter` fails |
| 2 | `ruff check`, `ruff format`, `mypy --strict` pass | pre-commit fails |
| 3 | Every adapter lives at `adapters/<port_stem>/<impl>.py` AND inherits from a Protocol declared in `ports/<port_stem>.py` | `lint_arch.py` fails |
| 4 | Cross-module imports go through the neighbor's `__init__.py` only — never `from app.modules.<other>.adapters/services/models/ports import …` | `lint_arch.py` fails |
| 5 | `app.infra.*` does not import from `app.modules` or `app.entrypoints` | `import-linter` fails |
| 6 | ORM tables (`__tablename__`, `class X(Base)`) live ONLY in `app/infra/db/tables/` | `lint_arch.py` fails |
| 7 | `get_session_maker()`, `Session()`, `session.commit/rollback/close()` appear ONLY in `adapters/*_uow/` | `lint_arch.py` fails |
| 8 | Max 3 args per function (PLR0913) — bundle 4+ params into a `@dataclass` / `BaseModel` | `ruff` fails |

When a linter fails, **fix the design**, not the linter.

Per-file ruff opt-outs (already configured in `pyproject.toml`): tests,
models, CLI commands, FastAPI route handlers — these legitimately need
many params.

---

## "I want to add X" — recipes

### Add a new domain module

1. `cp -r app/modules/example app/modules/<your-domain>` then rename
   the folder, files (`example_uow.py` → `<your-domain>_uow.py`),
   and class names inside.
2. Edit `app/modules/<your-domain>/__init__.py` — re-export only the
   public symbols (DTOs, Protocols, services, the `default_uow()`
   factory). Outside code can ONLY use what you export here.
3. Open `.importlinter` and add three lines under
   `[importlinter:contract:1-services-no-infra] source_modules`:
   ```
   app.modules.<your-domain>.services
   app.modules.<your-domain>.models
   app.modules.<your-domain>.ports
   ```
4. Run `pre-commit run --all-files`. Fix what fails.

### Add a new repository to an existing module

1. Define the Protocol: `app/modules/<m>/ports/<thing>_journal.py`
   ```python
   from typing import Protocol, runtime_checkable
   from app.modules.<m>.models.<thing> import Thing

   @runtime_checkable
   class ThingJournalRepository(Protocol):
       async def get(self, id: int) -> Thing | None: ...
       async def add(self, thing: Thing) -> None: ...
   ```
2. Implement the adapter: `app/modules/<m>/adapters/<thing>_journal/sqla.py`
   — folder name MUST equal the port file stem (`<thing>_journal`).
   The class MUST inherit from `ThingJournalRepository`. Take a
   session in `__init__`. **Never call `session.commit/rollback/close`**
   here — the UoW owns the session.
3. Wire it into the UoW adapter: in `adapters/<m>_uow/sqla.py`,
   inside `__aenter__`, add
   `self.things = SqlaThingJournalRepository(self._session)`.
4. Add the attribute to the UoW Protocol (`ports/<m>_uow.py`):
   `things: ThingJournalRepository`.

### Add a new use case (service)

```python
# app/modules/<m>/services/do_something.py
from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.modules.<m>.ports.<m>_uow import <M>UoW

async def do_something(uow: <M>UoW, name: str) -> None:
    async with uow:
        # ... orchestrate ports ...
        await uow.commit()       # REQUIRED — exit defaults to rollback
```

Three rules:
- Type-hint against the **Protocol**, not the concrete UoW.
- The `async with uow:` block scopes one transaction.
- `await uow.commit()` is **mandatory** at the end of a successful
  use case. Forgetting it = silent rollback (the safe failure mode).

### Add a new ORM table

1. `app/infra/db/tables/<thing>.py`:
   ```python
   from sqlalchemy.orm import Mapped, mapped_column
   from app.infra.db.tables.base import Base

   class ThingRow(Base):
       __tablename__ = "things"
       id: Mapped[int] = mapped_column(primary_key=True)
       name: Mapped[str]
   ```
2. Re-export it in `app/infra/db/tables/__init__.py` so Alembic
   autogenerate sees it.
3. **Never** `from app.infra.db.tables.thing import ThingRow` inside
   `services/`/`models/`/`ports/` — only adapters touch ORM rows.

### Add a CLI command or HTTP endpoint

`app/entrypoints/cli/main.py` (Typer) or `app/entrypoints/api/server.py`
(FastAPI). Entrypoints ARE the composition root — they're allowed to
import concrete adapters and instantiate `default_uow()`. Just call
the service:

```python
@app.post("/things")
async def create_thing(req: CreateRequest):
    item = await create_item(default_uow(), name=req.name)
    return {"id": item.id}
```

---

## Before you say "done"

Run the full check loop. If any of these fails, you are NOT done:

```bash
.venv/bin/python scripts/lint_arch.py
.venv/bin/lint-imports --config .importlinter
.venv/bin/ruff check app/ scripts/ tests/
.venv/bin/ruff format --check app/ scripts/ tests/
.venv/bin/mypy app/
.venv/bin/python -m pytest tests/ -q
```

Or just: `pre-commit run --all-files`.

When ruff/format complain, run with `--fix` and re-format. When the
arch linters complain, **fix the design** — do not edit the linter to
silence it.

---

## Cosmic Python UoW — why it looks weird

The pattern intentionally requires explicit `await uow.commit()`:

```python
async def buy_item(uow):
    async with uow:
        await uow.items.reserve(...)
        # if I forget commit here, NOTHING persists.
        # That's the desired behavior.
```

The opposite design ("commit on successful exit") means a forgotten
commit silently saves half-baked intermediate state — which is almost
always worse than rolling back. Keep it explicit.

The repository (`adapters/<port>/sqla.py`) takes the session in
`__init__` and uses it directly. **No commits inside the repository.**
The service decides when the transaction ends.

---

## Common mistakes to avoid

- **"I'll just import the SqlaXRepository directly in the service."**
  No — services type-hint against the Protocol. Otherwise rule 1
  fails (sqlalchemy in the application layer) and the service is no
  longer testable without a real DB.

- **"I'll commit inside the repo so the caller doesn't have to."**
  No — rule 7 fails. Sessions/commits live in `_uow/` only. Repos
  do `await self._session.flush()` (no commit) when they need an
  ID populated mid-transaction.

- **"This module needs something from another module — I'll import
  `app.modules.other.services.foo` directly."** No — rule 4 fails.
  Either re-export `foo` from `other`'s `__init__.py`, or rethink
  whether the modules should share that logic at all.

- **"The function needs 6 parameters."** No — bundle them into a
  `@dataclass` or pydantic model. The call site becomes
  `do_thing(Request(...))` instead of `do_thing(a, b, c, d, e, f)`.
  Rule 8 enforces this.

- **"Mypy is being annoying about this Optional, I'll add `# type: ignore`."**
  Don't. mypy --strict is set for a reason. Make the type honest.

- **"The pre-commit hook is in my way, I'll commit with `--no-verify`."**
  Don't. The hook is the reason the codebase stays sane. If a hook
  is genuinely wrong for your case, fix the hook config and commit
  the config change too.

---

## When you genuinely don't know

Read [ARCHITECTURE.md](ARCHITECTURE.md). It explains every rule with
*why it exists*, *what it catches*, and *when (if ever) to opt out*.

Then look at `app/modules/example/` — it's the canonical reference
implementation of every rule. Your new module should look like it.
