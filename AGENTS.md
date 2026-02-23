## Llamora Agent Guide

### Runtime & Tooling

* Fully offline by default. Any OpenAI-compatible endpoint (`llama.cpp` default).
* Python is managed via `uv`. `pyproject.toml` currently allows `>=3.11`.
* Always run Python tooling as `uv run …`.
* Frontend bundling is done by `scripts/build_assets.py` (esbuild). Native ES modules are used in development.
* Logs via `logger = getLogger(__name__)`, not `print`.
* Server CLI entrypoint: `uv run llamora-server dev|prod` (`src/llamora/__main__.py`).
* Lint/format:
  * Frontend: `biome check`, `biome format --write`.
  * Backend: `uv run ruff check`, `uv run ruff format`.
  * Types: `uv run pyright`.
  * Git hooks in `.githooks/` run both (enable with `git config core.hooksPath .githooks`).

---

### 📁 Layout

* `src/llamora/`: app factory, routes, services, llm, persistence.
* `config/`: Dynaconf (`LLAMORA_LLM__UPSTREAM__HOST` style env vars).
* `migrations/`: schema + migrations (encrypt-safe).
* `scripts/`: CLI helpers.
* `frontend/static/`: source JS/CSS/assets (and vendored JS under `frontend/static/js/vendor/`).
* `frontend/dist/`: optional bundled assets + `manifest.json` (used when present).
* `src/llamora/app/templates/`: server-rendered HTML (`pages/`, `views/`, `components/`, `layouts/`).

---

### 🧠 Backend Patterns

* Use type hints, `slots=True`, and per-module loggers.
* Routes are `async`. Use `await render_template()`.
* App-wide services are built in `AppServices.create()` and started/stopped via `AppLifecycle`.
* In routes/APIs, access shared services via `get_services()` / `get_db()` helpers from `llamora.app.services.container` (backed by `app.extensions["llamora"]`).
* Read config via `settings`, not `os.environ`, etc.
* Encryption = non-negotiable. Never store plaintext.
* `LocalDB` is the persistence facade; repositories hang off `db.users`, `db.entries`, `db.tags`, `db.vectors`, `db.search_history`.
* Migrations run automatically at startup and on DB init (`run_db_migrations`); use `scripts/migrate.py` for manual status/up.

---

### 🎨 Frontend Rules

* SSR HTML with HTMX. Prefer `hx-*` over fetch().
* Streams via `hx-ext="sse"`.
* JS = native ES modules (`type="module"`). Web Components for stateful UI (`extends HTMLElement`).
* Use `frontend/static/js/components/` for isolated UI.
* CSS uses custom tokens (`--color-*`), nesting allowed.
* Avoid duplicating markup. Partial everything.
* Build pipeline writes `frontend/dist/manifest.json`; app serves `frontend/dist` first, then falls back to `frontend/static`.

---

### 🧘 UI Philosophy

* Calm first. No spinners, popups, flash.
* Motion should be soft and minimal.
* Layouts should breathe across all themes.
* Visuals earn their space or get cut.
* If it feels anxious, strip it back.

---

### 🌳 Persistence

* Use `LocalDB` async wrapper; never touch SQLite raw.
* All data = encrypted at rest. Decrypt in-memory only.
* Embeddings are generated via `fastembed` (`TextEmbedding`) and indexed with HNSW.
* Embedding model warm-up runs once in background via `AppLifecycle`.
* Migrations must not break existing encrypted content.
* DEK storage modes: `session` (SQLite-backed server-side DEK) or `cookie` (stateless, DEK in encrypted cookie). Session mode can work across workers because `dek_sessions` live in SQLite.
* Session inactivity policy is configured under `SESSION` (`idle_ttl`, `cookie_touch_interval`, `csrf_ttl`); auth cookies are refreshed on activity and expire after inactivity.
* `dek_sessions` are cleared on app startup/shutdown in `session` mode; cleanup is global to the shared DB, so restarts/worker shutdowns log out session-mode users.
