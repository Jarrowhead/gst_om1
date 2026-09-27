# GST Filing App

GST return filing platform for Indian businesses and CA firms — capture purchase/sale documents, extract line items, reconcile ITC, prepare GSTR-1 / GSTR-3B, and file directly via GSP.

**Stack:** Python 3.11 (FastAPI · SQLAlchemy · Alembic · PostgreSQL · Redis · MinIO) backend · Next.js 16 + TypeScript (App Router · Tailwind · shadcn/ui) frontend

## Build status

See **[docs/BUILD_TRACKER.md](docs/BUILD_TRACKER.md)** for live progress — phase summary, full task ledger, and what remains.

## Documentation

| Doc | Contents |
|---|---|
| [BUILD_TRACKER.md](docs/BUILD_TRACKER.md) | Progress tracker — what is done, what remains |
| [GST_BUILD_STATUS.md](docs/GST_BUILD_STATUS.md) | Detailed build status + hold record |
| [PRD.md](docs/PRD.md) | Product requirements and user journeys |
| [TECHNICAL_ARCHITECTURE.md](docs/TECHNICAL_ARCHITECTURE.md) | Service layout, schemas, stack decisions |
| [API_SPECIFICATION.md](docs/API_SPECIFICATION.md) | Endpoint contracts |
| [SECURITY_AND_ACCESS.md](docs/SECURITY_AND_ACCESS.md) | Auth model, tenancy isolation, audit rules |
| [FRONTEND_SPECIFICATION.md](docs/FRONTEND_SPECIFICATION.md) | Screens, routes, design tokens |
| [EXTRACTION_SPEC.md](docs/EXTRACTION_SPEC.md) | Document extraction pipeline |
| [TESTING_STRATEGY.md](docs/TESTING_STRATEGY.md) | Test layers and quality gates |
| [AI_BUILD_PLAYBOOK.md](docs/AI_BUILD_PLAYBOOK.md) | How the build loop operates |

## Local development

```bash
# 1. Infrastructure — PostgreSQL:5436, Redis:6380, MinIO:9001
./backend/.venv/Scripts/python.exe scripts/bootstrap_stack.py

# 2. Backend
cd backend
./.venv/Scripts/python.exe -m uvicorn app.main:app --reload

# 3. Frontend (port 9094)
cd frontend
npm run dev
```

## Quality gates

Every task must pass before it is counted as done:

```bash
cd backend && ./.venv/Scripts/python.exe -m pytest tests/ -q   # tests
cd backend && ./.venv/Scripts/python.exe -m ruff check .        # lint
cd backend && ./.venv/Scripts/python.exe -m mypy .              # types
cd frontend && npx tsc --noEmit && npm run lint                 # frontend
```
