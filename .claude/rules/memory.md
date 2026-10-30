---
paths:
  - "memory/**"
---

# Persistent Memory & SQLite Rules

1. **Idempotent Migrations**:
   - All database schema updates must be idempotent (e.g. check `metadata` key or table existence before running DDL).
   - Never duplicate session logs or preferences across reload/re-migration cycles.

2. **Database Isolation**:
   - Automated tests touching memory must always re-point `memory.ledger.DB_PATH` to a temporary directory (`tests/conftest.py` does it for every test).
   - Never run tests against the working tree `memory/kurek_memory.db`.
