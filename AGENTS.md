## Llamora Agent Guide

### Runtime & Tooling

* Fully offline. Local LLM only (`llama.cpp` or `llamafile`).
* Use Python 3.11+, managed via `uv`. Always `uv run …`.
* No tests. Manual QA only.
* No bundler. ES modules + CSS are handcrafted.
* Logs via `logger = getLogger(__name__)`, not `print`.

---

### 📁 Layout

* `src/llamora/`: app, services, crypto, db logic.
* `config/`: Dynaconf (`LLAMORA__FOO__BAR`).
* `sql/schema.sql`: schema (encrypt-safe).
* `scripts/`: CLI helpers.
* `frontend/static/`: JS/CSS/assets.
* `templates/partials/`: server-rendered HTML.

---

### 🧠 Backend Patterns

* Start files with `from __future__ import annotations`.
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
* JS = native ES modules (`type="module"`). No deps.
* Use `frontend/static/js/components` for isolated UI.
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
