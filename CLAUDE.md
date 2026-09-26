# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install
uv sync

# Run dev server (live reload)
uv run llamora-server dev              # add --no-reload to disable watcher
QUART_DEBUG=1 uv run llamora-server dev  # verbose Quart output

# Run production server
uv run llamora-server prod

# Lint & format
uv run ruff check                      # backend lint
uv run ruff format                     # backend format
biome check                            # frontend lint
biome format --write                   # frontend format
uv run pyright                         # type check

# Tests
uv run pytest                          # run all tests
uv run pytest path/to/test.py -k name  # single test
uv run pytest -m "not e2e"             # skip browser tests

# End-to-end (Playwright; first time: uv run playwright install chromium)
uv run pytest tests/e2e -n 3           # isolated server + temp DB + fake LLM per worker
uv run pytest tests/e2e --headed --slowmo 300   # watch it run
uv run pytest tests/e2e --e2e-no-build # skip the prod asset build
uv run playwright show-trace test-results/<test>/trace.zip  # failure traces
# -n 3 is the sweet spot (~2.3x faster than serial); more workers contend on
# Argon2 (256 MiB per derivation) and each worker's embedding model.

# Frontend assets
uv run python scripts/build_assets.py watch --mode dev   # dev bundle + watch
uv run python scripts/build_assets.py build --mode prod  # production bundle

# Vendored JS (only when updating vendor libs)
pnpm install && pnpm vendor

# Migrations (auto-run at startup; manual inspection only)
uv run python scripts/migrate.py status
uv run python scripts/migrate.py up

# Git hooks
git config core.hooksPath .githooks    # pre-commit: ruff + biome on staged files
```

## Architecture

**Stack:** Quart (async Python) + HTMX + SQLite + libsodium/PyNaCl encryption. No JS framework — native ES modules and Web Components. Any OpenAI-compatible `/v1/chat/completions` endpoint for inference (default: llama.cpp).

### Backend (`src/llamora/`)

- **Entry point:** `__main__.py` — CLI via Typer (`llamora-server dev|prod`)
- **App factory:** `app/__init__.py` → `create_app()`
- **Routes:** `app/routes/` — async handlers (auth, entries, entries_stream, tags, days, search). SSE streaming for model responses.
- **Services:** `app/services/` — business logic. `AppServices.create()` builds all services; `AppLifecycle` manages startup/shutdown. Access in routes via `get_services()` / `get_db()` from `llamora.app.services.container`.
- **Persistence:** `persistence/local_db.py` → `LocalDB` facade. Repositories: `db.users`, `db.entries`, `db.tags`, `db.vectors`, `db.search_history`, `db.sessions`, `db.ttl_store`. Raw SQL, no ORM.
- **LLM:** `llm/` — prompt templates (Jinja2 in `llm/templates/`), tokenizer configs, OpenAI client wrapper. Upstream URLs (`LLM.upstream.host`, `LLM.chat.base_url`, `/v1`) are resolved only in `llm/endpoints.py`.
- **Config:** Dynaconf. `config/settings.toml` (defaults) → `config/settings.local.toml` (overrides) → env vars (`LLAMORA_LLM__UPSTREAM__HOST` style). Read via `settings`, never `os.environ`.
- **Images:** `app/services/images/` (processing, file format, blob store, `ImageService`), rows in `db.images`, routes in `routes/images.py`. Files live under `IMAGES.path`, encrypted with per-image keys wrapped by the DEK.
- **Encryption:** All content encrypted at rest (XChaCha20-Poly1305 + AAD, per-record nonce). Password → Argon2ID → wrapping key → DEK (in-memory only). Migrations must never break encrypted content.

### Frontend (`frontend/`)

- **Source:** `frontend/static/js/` — ES modules, Web Components in `components/`
- **Entry:** `app-entry.js` → `app-init.js` (init order) → `lifecycle.js` (HTMX events, dispatches `app:rehydrate`)
- **State:** `services/app-state.js` is the canonical state registry with three tiers:
  - **Frame State** (URL-derived): `view`, `day`, `selectedTag`, `target` — hydrated from `<script id="view-state">` JSON on every server response, reset on HTMX swap
  - **Preference State** (sessionStorage): sort order, scroll positions — survives navigation
  - **Ephemeral State** (component-only): phase machines, filter text, DOM refs — never persisted
- **Templates:** `src/llamora/app/templates/` — Jinja2 (`pages/`, `views/`, `components/`, `layouts/`)
- **Bundling:** esbuild via `scripts/build_assets.py`. When `frontend/dist/manifest.json` exists, app serves bundles; otherwise falls back to `frontend/static/`.

### Key patterns

- Routes are `async`; use `await render_template()`.
- Use type hints and `slots=True` on dataclasses.
- Logging: `logger = getLogger(__name__)`, never `print`.
- SSR HTML with HTMX. Prefer `hx-*` attributes over `fetch()`. SSE via `hx-ext="sse"`.
- CSS uses custom tokens (`--color-*`), nesting allowed.
- New component bootstrap: listen to `app:rehydrate`, read `event.detail.frame`.
- Cache invalidation is managed by `services/cache_registry.py`.

### UI philosophy

Calm first. No spinners, popups, flash. Motion should be soft and minimal. Visuals earn their space or get cut.
