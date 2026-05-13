# Alembic migrations

Run `alembic init -t async migrations` from the repo root (with the
working directory at `template/`) to scaffold this folder properly,
then point `env.py` at `app.infra.db.tables.Base.metadata`.

Conventional commands once set up:

```bash
# Generate a new migration from current models
alembic revision --autogenerate -m "add items table"

# Apply pending migrations
alembic upgrade head

# Roll back one revision
alembic downgrade -1
```
