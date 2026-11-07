---
status: accepted
---

# Consolidate runtime state into one asynchronous ORM database

Three separate synchronous SQLite databases (task queue / API usage / Agent sessions) could not switch to PostgreSQL using one `DATABASE_URL`, share foreign keys/transactions across databases, or avoid blocking the event loop in async FastAPI routes. Cut over to one SQLAlchemy asynchronous database, with one `DATABASE_URL` choosing SQLite for development or PostgreSQL for production. Provide a one-time migration without retaining handwritten SQL paths. A hard cutover avoids intermediate dual writes in exchange for unified transactions and database portability.

## Consequences

- Migration cannot be rolled back. Once every Repository/Worker is asynchronous and relies on single-database transactions, reverting to multiple databases or synchronous drivers requires rewriting the entire data-access layer.
- Storage remains explicitly layered: runtime state goes into DB; project data (project.json / scripts / media files) stays in the filesystem.
