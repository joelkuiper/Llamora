# Llamora Agent Guide

Welcome.
This document captures the implicit conventions that make Llamora work as a coherent whole.
Follow these notes to extend or repair the system without breaking its calmness or intent.

---

## Environment and Tooling

* Llamora runs entirely offline. Do not depend on any remote service, telemetry, or package download. The only external process is the local `llama.cpp` or `llamafile` model server.
* Python is managed through **uv**. Always prefer `uv run ...` to ensure isolation and reproducibility. The expected interpreter is Python 3.11 or newer.
* There are no automated tests. Verification is manual and deliberate.
* Dependencies are treated as frozen. Use the standard library or existing helpers from `llamora.util` before adding anything new.
* The front end ships complete. There is no Node.js toolchain and no bundler. ES modules and CSS are written by hand.
* For diagnostics use the logging system, not print statements. `LOG_LEVEL=DEBUG` enables detailed logs when needed.

---

## Repository Layout

* `src/llamora/` contains the Quart application, services, persistence layer, crypto logic, and utilities.
* `config/` holds Dynaconf configuration. Follow the `LLAMORA_<SECTION>__<SUBSECTION>` pattern when introducing new keys.
* `sql/schema.sql` defines the SQLite database. Changes must remain compatible with encrypted payloads.
* `scripts/` provides small maintenance utilities and reference patterns for database operations.
* Static files live in `src/llamora/app/static/`. Templates and partials are under `src/llamora/app/templates/`.

---

## Backend Conventions

* Begin new Python files with

  ```python
  from __future__ import annotations
  ```

  and provide full type hints. Dataclasses use `slots=True`.
* Create a module logger at the top:

  ```python
  import logging
  logger = logging.getLogger(__name__)
  ```
* Routes are asynchronous. Use `await render_template()` and avoid blocking I/O.
* Access shared resources through the service container (`AppServices`). Do not create `LocalDB` or `LLMService` directly inside route functions.
* Configuration should always come from `settings` (Dynaconf). Avoid `os.environ` reads; declare new variables in `config/settings.toml`.
* Respect the encryption pipeline. Secrets and message data are handled by helpers in `auth_helpers`, `crypto`, and `persistence`. Never store plaintext.
* The SSE stream currently processes one request at a time. Future work may add concurrency management using an async semaphore or slot pool based on `LLAMORA_LLM__SERVER__PARALLEL`.
* Write small composable utilities. Use clear naming (`get_`, `render_`, `*_service`) to keep call sites predictable.

---

## Frontend Conventions
* Front-end code lives in `src/llamora/app/static/`.
* The interface is server-rendered HTML enhanced with **HTMX**. Use attributes like `hx-get`, `hx-swap`, and `hx-target` rather than custom fetch code.
* Streaming responses use **Server-Sent Events (SSE)** through `hx-ext="sse"`.
* JavaScript files are plain **ES modules** loaded with `type="module"`. Avoid third-party code and prefer small, clear functions.
* Prefer to use web components for UI widgets, (see `src/llamora/app/static/js/components`)
* CSS uses a **design-token system** in `main.css`. Extend `--color-*`, `--accent-*`, etc. variables instead of adding fixed constants.
* CSS uses nesting which is supported in modern browsers, follow that convention.
* Templates rely on partials under `src/llamora/app/templates/partials`. Always reuse fragments rather than duplicating markup.

---

## UI and UX Principles

* The UI should feel calm, responsive, and deliberate. Avoid sudden motion, jitter, or pop-ups.
* Use subtle transitions, soft fades, and consistent timing. Feedback should inform quietly rather than demand attention.
* Layout rhythm matters. Keep padding, margins, and font weights balanced across light and dark modes.
* Favor emptiness over clutter. Every visual element should justify its existence.
* The design language aims for emotional stability. Users should feel that nothing urgent is happening, even when the model is busy.

---

## Data and Persistence

* SQLite is the database, accessed asynchronously through `LocalDB`. Use its API to maintain encryption and pooling.
* All content in storage is encrypted. Decryption happens only in memory.
* Vector search and embedding services initialize once at startup. Register any background components in `AppLifecycle` so they start and stop correctly.
* Schema or crypto changes must preserve backward compatibility so older entries can still decrypt and load.
