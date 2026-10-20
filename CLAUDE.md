# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Layout

Two independent packages with separate toolchains and **no root-level orchestration** — every command runs from inside its package directory.

- `backend/` — Python 3.11, uv-managed. FastAPI + SQLAlchemy async + Celery (Redis broker) + PostgreSQL 18. The MVP loop (projects/environments/variables/cases CRUD, DSL execution engine in `app/engine/`, run reports) is implemented; see `docs/design/` for the architecture.
- `frontend/` — Vue 3 + TypeScript + Vite + Pinia + Vue Router + Element Plus. Admin layout with routes; API types generated from the backend's OpenAPI schema (`npm run gen:api`).
- `docs/` — design documentation. **This is the source of truth for what to build.**

## Documentation

Before writing code, read [docs/README.md](docs/README.md) for the index; the design docs there specify the target architecture, data model, execution engine, and API — follow them rather than inventing an alternative.

All documentation lives under `docs/`, in a subdirectory by type (`research/`, `design/`, `api/`, `ops/`, `adr/`, `guide/`, `agents/`). Add new docs to the matching directory and link them from `docs/README.md`. Do not write docs elsewhere in the repo.

Design docs are updated **in the same change** as the code they describe.

## Commands

### backend/

```sh
cd backend
uv run uvicorn app.main:app --port 8000   # API server (needs compose services up)
uv run celery -A app.tasks.celery_app worker -Q execution,default -P solo  # worker
uv run alembic upgrade head             # apply migrations (PYTHONUTF8=1 on Windows)
uv run python scripts/seed.py           # reset business tables to the demo dataset
uv sync                                 # install/refresh dependencies into backend/.venv
```

`uv run` and `uv sync` create `backend/.venv` on first use (gitignored). **Run uv from `backend/`** — from the repo root it finds no `pyproject.toml` and falls back to an ephemeral environment instead of the project's.

### frontend/

```sh
cd frontend
npm install
npm run dev             # Vite dev server (proxies /api to 127.0.0.1:8000)
npm run build           # type-check + vite build, in parallel
npm run type-check      # vue-tsc --build
npm run lint            # oxlint --fix, then eslint --fix (sequential)
npm run format          # prettier over src/ only
npm run test            # vitest run
npm run gen:api         # regenerate src/api/schema.d.ts from a running backend
```

Use `npm run type-check`, not `tsc` — plain `tsc` cannot resolve types across `.vue` imports.

`.npmrc` sets `legacy-peer-deps=true`: openapi-typescript 7.x peers on TS 5 while the project uses TS 6 (the generator CLI is decoupled from the host TS; verified working).

## Testing

- **backend**: pytest (`uv run pytest`) — unit tests in `tests/engine/` (pure, MockTransport), integration tests in `tests/integration/` (real PostgreSQL via compose, eager Celery), e2e in `tests/e2e/` (real sockets; excluded by default, run with `-m e2e`).
- **frontend**: Vitest (`npm run test`) — tests live next to source as `*.spec.ts`. Two seams (see `docs/design/前端设计.md` §7): Pinia stores with an injected mock API client, and key component mounts.

## Conventions

**Linting is layered, not redundant.** oxlint owns correctness rules (`.oxlintrc.json`), ESLint is wired with `eslint-plugin-oxlint` so it does not re-report what oxlint already caught, and `eslint-config-prettier` stops it fighting Prettier. Formatting is Prettier's job; don't add style rules to ESLint.

**Formatting**: no semicolons, single quotes, 100-column width (`.prettierrc.json`). `.editorconfig` sets 2-space indent and LF. Note `npm run format` only covers `src/`, so config files at the frontend root are not auto-formatted.

**TypeScript strictness**: `tsconfig.app.json` enables `noUncheckedIndexedAccess`, so `arr[i]` and `obj[key]` are `T | undefined`. Handle the undefined case rather than asserting it away.

**Path alias**: `@` maps to `src/`, declared in *both* `vite.config.ts` (for resolution) and `tsconfig.app.json` (for types). Change both together or the two disagree.

**Node version**: `^22.18.0 || >=24.12.0` (`package.json` engines).

## Agent skills

### Issue tracker

Issues and specs live in GitHub Issues (`wanglin741/api-test-platform`), via the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels

The five canonical triage roles map to identical label strings (`needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`). See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: vocabulary from `docs/design/` (Chinese design docs), ADRs in `docs/adr/`. See `docs/agents/domain.md`.
