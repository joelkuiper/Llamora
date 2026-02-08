#!/usr/bin/env bash
set -euo pipefail

files="$(rg --files \
  -g '*.js' -g '*.mjs' -g '*.cjs' \
  -g '!frontend/static/js/vendor/**' \
  -g '!**/*.min.js' \
  -g '!**/*.umd.js' \
  -g '!**/*.jsm.js' \
  frontend/static/js)"

if [[ -z "${files}" ]]; then
  exit 0
fi

quick-lint-js ${files}
