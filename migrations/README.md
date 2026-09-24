# Migrations

Alembic is the source of truth for production schema changes.

```bash
# apply latest
alembic upgrade head

# create a new revision after changing app/models/*
alembic revision --autogenerate -m "describe change"
```

Fresh dev/Railway deploys also run `Base.metadata.create_all()` at startup,
so a brand-new database works even before migrations run. Always add a
migration for schema changes — do not rely on `create_all` for upgrades.
