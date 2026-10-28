---
paths:
  - "memory/**"
---

# Persistent Memory & SQLite Rules

1. **Idempotent Migrations**:
   - All database schema updates must be idempotent (e.g. check `metadata` key or table existence before running DDL).
   - Never duplicate session logs or preferences across reload/re-migration cycles.

2. **Database Isolation**:
   - Automated tests touching memory must always re-point `memory_manager.DB_PATH` and `memory_manager.MEMORY_PATH` to temporary directories.
   - Never run tests against the working tree `memory/kurek_memory.db`.
