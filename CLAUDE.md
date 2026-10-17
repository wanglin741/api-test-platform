# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Layout

Two independent packages with separate toolchains and **no root-level orchestration** — every command runs from inside its package directory.

- `backend/` — Python 3.11, uv-managed. A bare scaffold: `pyproject.toml` declares `dependencies = []`, so there is no web framework, no routes, and no persistence layer yet. `main.py` prints a greeting; `app/` and `app/api/` are empty packages.
- `frontend/` — Vue 3 + TypeScript + Vite + Pinia + Vue Router. Also a scaffold: `src/router/index.ts` registers zero routes, and there is no API client.
- `docs/` — design documentation. **This is the source of truth for what to build.**

Both packages are placeholder shells. Anything beyond the commands below is still to be built, so confirm a dependency or directory exists before assuming it does.

## Documentation

The platform is **designed but not implemented**. Before writing code, read [docs/README.md](docs/README.md) for the index; the design docs there specify the target architecture, data model, execution engine, and API — follow them rather than inventing an alternative.

All documentation lives under `docs/`, in a subdirectory by type (`research/`, `design/`, `api/`, `ops/`, `adr/`, `guide/`). Add new docs to the matching directory and link them from `docs/README.md`. Do not write docs elsewhere in the repo.

Design docs are updated **in the same change** as the code they describe.

## Commands

### backend/

```sh
cd backend
uv run main.py          # run the entrypoint
uv sync                 # install/refresh dependencies into backend/.venv
```

`uv run` and `uv sync` create `backend/.venv` on first use (gitignored). **Run uv from `backend/`** — from the repo root it finds no `pyproject.toml` and falls back to an ephemeral environment instead of the project's.

There is no `uv.lock` in git yet; it appears after the first sync and should be committed.

### frontend/

```sh
cd frontend
npm install
npm run dev             # Vite dev server
npm run build           # type-check + vite build, in parallel
npm run type-check      # vue-tsc --build
npm run lint            # oxlint --fix, then eslint --fix (sequential)
npm run format          # prettier over src/ only
```

Use `npm run type-check`, not `tsc` — plain `tsc` cannot resolve types across `.vue` imports.

## Testing

**Neither package has a test runner installed.** No pytest on the backend, no Vitest on the frontend. There is no `npm test` script and no single-test command to reach for. Adding tests means choosing and installing a runner first.

## Conventions

**Linting is layered, not redundant.** oxlint owns correctness rules (`.oxlintrc.json`), ESLint is wired with `eslint-plugin-oxlint` so it does not re-report what oxlint already caught, and `eslint-config-prettier` stops it fighting Prettier. Formatting is Prettier's job; don't add style rules to ESLint.

**Formatting**: no semicolons, single quotes, 100-column width (`.prettierrc.json`). `.editorconfig` sets 2-space indent and LF. Note `npm run format` only covers `src/`, so config files at the frontend root are not auto-formatted.

**TypeScript strictness**: `tsconfig.app.json` enables `noUncheckedIndexedAccess`, so `arr[i]` and `obj[key]` are `T | undefined`. Handle the undefined case rather than asserting it away.

**Path alias**: `@` maps to `src/`, declared in *both* `vite.config.ts` (for resolution) and `tsconfig.app.json` (for types). Change both together or the two disagree.

**Node version**: `^22.18.0 || >=24.12.0` (`package.json` engines).
