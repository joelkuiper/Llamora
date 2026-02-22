## Llamora Agent Guide

### Runtime & Tooling

* Fully offline by default. Any OpenAI-compatible endpoint (`llama.cpp` default).
* Python 3.11+, managed via `uv`. Always `uv run …`.
* Frontend bundled with `esbuild` (`scripts/build_assets.py`). Native ES modules in development.
* Logs via `logger = getLogger(__name__)`, not `print`.
* Lint/format:
  * Frontend: `biome check`, `biome format --write`.
  * Backend: `uv run ruff check`, `uv run ruff format`.
  * Git hooks in `.githooks/` run both (enable with `git config core.hooksPath .githooks`).

---

### 📁 Layout

* `src/llamora/`: app, services, crypto, db logic.
* `config/`: Dynaconf (`LLAMORA__FOO__BAR`).
* `migrations/`: schema + migrations (encrypt-safe).
* `scripts/`: CLI helpers.
* `frontend/static/`: JS/CSS/assets.
* `src/llamora/app/templates/`: server-rendered HTML (`pages/`, `views/`, `components/`, `layouts/`).

---

### 🧠 Backend Patterns

* Use type hints, `slots=True`, and per-module loggers.
* Routes are `async`. Use `await render_template()`.
* Access shared services via `AppServices`, never direct inits.
* Read config via `settings`, not `os.environ`, etc.
* Encryption = non-negotiable. Never store plaintext.
* Utils should be small + named (`get_`, `render_`, etc.).

---

### 🎨 Frontend Rules

* SSR HTML with HTMX. Prefer `hx-*` over fetch().
* Streams via `hx-ext="sse"`.
* JS = native ES modules (`type="module"`). Web Components for stateful UI (`extends HTMLElement`).
* Use `frontend/static/js/components/` for isolated UI.
* CSS uses custom tokens (`--color-*`), nesting allowed.
* Avoid duplicating markup. Partial everything.

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
* Vectors boot once via `AppLifecycle`.
* Migrations must not break existing encrypted content.
