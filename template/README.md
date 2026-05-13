# Project template — modular monolith with hexagonal architecture

A reusable starter for any business project. Pre-wired with:

- **Hexagonal architecture** (ports + adapters) inside each module
- **Vertical slicing** by domain (each module is independently deployable in spirit)
- **Cosmic Python Unit of Work** pattern for persistence
- **8 architecture rules**, enforced via 3 linters in pre-commit
- **Negative tests** that prove each rule actually catches violations

Designed for: an async Python 3.12+ codebase with SQLAlchemy + FastAPI + Typer.
Drop the bits you don't need.

## What's in here

```
template/
├── README.md                 ← you are here
├── ARCHITECTURE.md           ← deep dive on the 8 rules
├── pyproject.toml            ← ruff + mypy + pytest config
├── .importlinter             ← rules 1, 5
├── .pre-commit-config.yaml   ← wires ruff/mypy/import-linter/lint_arch
├── app/
│   ├── infra/                ← bottom layer: db engine + tables + migrations
│   ├── modules/example/      ← copy this folder to scaffold a new domain
│   └── entrypoints/          ← composition root: CLI + FastAPI
├── scripts/
│   └── lint_arch.py          ← custom AST linter for rules 3, 4, 6, 7
└── tests/
    └── test_lint_arch.py     ← negative tests for the AST linter
```

## Quickstart

```bash
# 1. Copy the template into a new project
cp -r template ../my-new-project && cd ../my-new-project

# 2. Edit pyproject.toml — replace `your-project-name`, trim deps you don't need

# 3. Install
uv venv && source .venv/bin/activate
uv pip install -e .[dev]

# 4. Wire pre-commit hook
pre-commit install

# 5. Verify all linters pass on the empty template
.venv/bin/python scripts/lint_arch.py
.venv/bin/lint-imports --config .importlinter
.venv/bin/python -m pytest tests/

# 6. Rename `app/modules/example` to your first real domain
mv app/modules/example app/modules/billing  # or whatever
# ...then update the module's __init__.py exports + the import-linter
# config to add billing.{services,models,ports} as sources for rule 1.
```

## Adding a new module

1. `cp -r app/modules/example app/modules/<your-domain>` then rename inside
2. Define ports (Protocols) in `ports/`. UoW port file MUST end with `_uow.py`
3. Implement adapters in `adapters/<port_name>/`. Adapter folder MUST match port file stem
4. Write services in `services/` — they take a UoW Protocol, never a concrete class
5. Add the module's `services`/`models`/`ports` packages to `.importlinter` rule 1
6. Run `pre-commit run --all-files` — it'll catch any rule violations

## The 8 rules in one sentence each

| # | Rule | Enforced by |
|---|------|-------------|
| 1 | services/models/ports must NOT import infra | `import-linter` |
| 2 | PEP/style/types pass | `ruff` + `mypy` |
| 3 | Adapter folder name == port file stem; class inherits from port Protocol | `lint_arch.py` |
| 4 | Cross-module imports via `__init__.py` only | `lint_arch.py` |
| 5 | `infra/` never imports from `modules/` or `entrypoints/` | `import-linter` |
| 6 | ORM tables ONLY in `app/infra/db/tables/` | `lint_arch.py` |
| 7 | Sessions/commits ONLY in `*_uow/` adapter files | `lint_arch.py` |
| 8 | Max 3 args per function (PLR0913) | `ruff` |

See [ARCHITECTURE.md](ARCHITECTURE.md) for the long version of each rule —
why it exists, what it catches, when to opt out.

## Why each rule

The short version:

- **Rule 1** keeps the application layer testable without spinning up a DB
- **Rule 2** is table stakes
- **Rule 3** makes the codebase navigable — `port_name` reveals the implementations
- **Rule 4** keeps modules independently evolvable; today's "internal helper" stays internal
- **Rule 5** prevents circular dependencies between layers
- **Rule 6** keeps Alembic autogenerate reliable (single `Base.metadata`)
- **Rule 7** keeps the UoW the ONLY place where transactions begin and end
- **Rule 8** forces DTO/dataclass extraction once functions get parameter-heavy

## Where to look

- [`app/modules/example/`](app/modules/example/) — pattern to copy when adding a domain
- [`app/infra/db/`](app/infra/db/) — engine + Base; add tables here
- [`scripts/lint_arch.py`](scripts/lint_arch.py) — the custom AST linter
- [`tests/test_lint_arch.py`](tests/test_lint_arch.py) — proves each rule catches its target

## License

Use as you wish.
