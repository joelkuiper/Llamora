from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Dict, Iterable, List

PROJECT_ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = PROJECT_ROOT / "frontend" / "static"
DIST_DIR = PROJECT_ROOT / "frontend" / "dist"
JS_ENTRY = STATIC_DIR / "js" / "app-entry.js"
CSS_ENTRIES_DIR = STATIC_DIR / "css" / "entries"
ESBUILD_BIN = PROJECT_ROOT / "scripts" / "bin" / "esbuild"
JS_ENTRY_ALIASES = {
    "app-entry": "app",
}

PASSTHROUGH_DIRECTORIES = [
    (STATIC_DIR / "img", DIST_DIR / "img"),
    (STATIC_DIR / "js" / "vendor", DIST_DIR / "js" / "vendor"),
    (STATIC_DIR / "fonts", DIST_DIR / "fonts"),
]
META_JS = DIST_DIR / "meta-js.json"
META_CSS = DIST_DIR / "meta-css.json"


class BuildError(RuntimeError):
    pass


def _ensure_esbuild() -> None:
    if not ESBUILD_BIN.exists():
        raise BuildError(
            "esbuild binary is missing. Run scripts/bin/esbuild once to download the vendor binary."
        )


def _discover_entries(directory: Path, suffix: str) -> List[Path]:
    if not directory.exists():
        return []
    return sorted(p for p in directory.glob(f"*.{suffix}") if p.is_file())


def _discover_js_entries() -> List[Path]:
    return [JS_ENTRY] if JS_ENTRY.exists() else []


def _esbuild_common_args(mode: str) -> List[str]:
    args: List[str] = [
        "--bundle",
        "--platform=browser",
        "--format=esm",
        "--target=es2022",
        "--log-level=info",
        "--loader:.svg=file",
        "--asset-names=../icons/[name]",
        "--entry-names=[name]-[hash]",
    ]
    if mode == "dev":
        args.append("--sourcemap")
    else:
        args.append("--minify")
    return args


def _run_esbuild(
    entries: Iterable[Path],
    outdir: Path,
    outbase: Path,
    mode: str,
    watch: bool,
    meta_path: Path | None = None,
) -> subprocess.Popen | None:
    entry_list = list(entries)
    if not entry_list:
        return None

    args = [str(ESBUILD_BIN), *map(str, entry_list)]
    args.extend(_esbuild_common_args(mode))
    args.extend(
        [
            f"--outdir={outdir}",
            f"--outbase={outbase}",
        ]
    )
    if meta_path is not None:
        args.append(f"--metafile={meta_path}")

    if watch:
        args.append("--watch")
        process = subprocess.Popen(args)
        return process

    result = subprocess.run(args, check=False)
    if result.returncode != 0:
        raise BuildError(f"esbuild failed with exit code {result.returncode}")
    return None


def _copy_passthrough_assets() -> None:
    for source, destination in PASSTHROUGH_DIRECTORIES:
        if not source.exists():
            continue
        if destination.exists():
            shutil.rmtree(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, destination)


def _load_metafile(meta_path: Path) -> dict[str, str]:
    if not meta_path.exists():
        return {}
    try:
        payload = json.loads(meta_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    outputs = payload.get("outputs", {})
    resolved: dict[str, str] = {}
    for output_path, entry in outputs.items():
        entry_point = entry.get("entryPoint")
        if not entry_point:
            continue
        entry_name = Path(entry_point).stem
        resolved[entry_name] = output_path
    return resolved


def _normalize_output_path(output_path: str) -> str:
    output = Path(output_path)
    if not output.is_absolute():
        output = (PROJECT_ROOT / output).resolve()
    try:
        output = output.relative_to(DIST_DIR)
    except ValueError:
        output = output.name
    return output.as_posix()


def _cleanup_outputs(valid_outputs: set[str], directory: Path, suffix: str) -> None:
    if not directory.exists():
        return
    for path in directory.glob(f"*.{suffix}"):
        rel = path.relative_to(DIST_DIR).as_posix()
        if rel not in valid_outputs:
            path.unlink()


def _write_manifest() -> None:
    manifest: Dict[str, Dict[str, str]] = {"js": {}, "css": {}}
    js_meta = _load_metafile(META_JS)
    css_meta = _load_metafile(META_CSS)
    valid_outputs: set[str] = set()

    for entry_name, output_path in js_meta.items():
        normalized = _normalize_output_path(output_path)
        mapped_name = JS_ENTRY_ALIASES.get(entry_name, entry_name)
        manifest["js"][mapped_name] = normalized
        valid_outputs.add(normalized)

    for entry_name, output_path in css_meta.items():
        normalized = _normalize_output_path(output_path)
        manifest["css"][entry_name] = normalized
        valid_outputs.add(normalized)

    if not manifest["js"] and (DIST_DIR / "js").exists():
        for path in sorted((DIST_DIR / "js").glob("*.js")):
            manifest["js"][path.stem] = path.relative_to(DIST_DIR).as_posix()
            valid_outputs.add(path.relative_to(DIST_DIR).as_posix())

    if not manifest["css"] and (DIST_DIR / "css").exists():
        for path in sorted((DIST_DIR / "css").glob("*.css")):
            manifest["css"][path.stem] = path.relative_to(DIST_DIR).as_posix()
            valid_outputs.add(path.relative_to(DIST_DIR).as_posix())

    if valid_outputs:
        _cleanup_outputs(valid_outputs, DIST_DIR / "js", "js")
        _cleanup_outputs(valid_outputs, DIST_DIR / "css", "css")

    manifest_path = DIST_DIR / "manifest.json"
    DIST_DIR.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def _clean_dist() -> None:
    if DIST_DIR.exists():
        shutil.rmtree(DIST_DIR)


def build(mode: str) -> None:
    _ensure_esbuild()
    DIST_DIR.mkdir(parents=True, exist_ok=True)

    js_out = DIST_DIR / "js"
    css_out = DIST_DIR / "css"
    js_out.mkdir(parents=True, exist_ok=True)
    css_out.mkdir(parents=True, exist_ok=True)

    js_entries = _discover_js_entries()
    css_entries = _discover_entries(CSS_ENTRIES_DIR, "css")

    _run_esbuild(js_entries, js_out, JS_ENTRY.parent, mode, watch=False, meta_path=META_JS)
    _run_esbuild(
        css_entries, css_out, CSS_ENTRIES_DIR, mode, watch=False, meta_path=META_CSS
    )

    _copy_passthrough_assets()
    _write_manifest()


def _snapshot_sources() -> Dict[str, float]:
    snapshot: Dict[str, float] = {}
    for source, _ in PASSTHROUGH_DIRECTORIES:
        if not source.exists():
            continue
        for path in source.rglob("*"):
            if path.is_file():
                snapshot[str(path)] = path.stat().st_mtime
    return snapshot


def _snapshot_outputs() -> Dict[str, float]:
    snapshot: Dict[str, float] = {}
    for directory in (DIST_DIR / "js", DIST_DIR / "css"):
        if not directory.exists():
            continue
        for path in directory.glob("*." + ("js" if directory.name == "js" else "css")):
            snapshot[str(path)] = path.stat().st_mtime
    for meta_path in (META_JS, META_CSS):
        if meta_path.exists():
            snapshot[str(meta_path)] = meta_path.stat().st_mtime
    return snapshot


def _watch_passthrough(stop_event: threading.Event) -> None:
    previous_sources = _snapshot_sources()
    previous_outputs = _snapshot_outputs()
    _copy_passthrough_assets()
    _write_manifest()
    while not stop_event.is_set():
        time.sleep(1.0)
        current_sources = _snapshot_sources()
        current_outputs = _snapshot_outputs()
        if current_sources != previous_sources:
            _copy_passthrough_assets()
            previous_sources = current_sources
            previous_outputs = _snapshot_outputs()
        if current_outputs != previous_outputs:
            _write_manifest()
            previous_outputs = current_outputs


def watch(mode: str) -> None:
    _ensure_esbuild()
    DIST_DIR.mkdir(parents=True, exist_ok=True)

    js_out = DIST_DIR / "js"
    css_out = DIST_DIR / "css"
    js_out.mkdir(parents=True, exist_ok=True)
    css_out.mkdir(parents=True, exist_ok=True)

    js_entries = _discover_js_entries()
    css_entries = _discover_entries(CSS_ENTRIES_DIR, "css")

    stop_event = threading.Event()
    watcher_thread = threading.Thread(
        target=_watch_passthrough, args=(stop_event,), daemon=True
    )
    watcher_thread.start()

    processes: List[subprocess.Popen] = []
    try:
        js_process = _run_esbuild(
            js_entries, js_out, JS_ENTRY.parent, mode, watch=True, meta_path=META_JS
        )
        css_process = _run_esbuild(
            css_entries,
            css_out,
            CSS_ENTRIES_DIR,
            mode,
            watch=True,
            meta_path=META_CSS,
        )
        if js_process:
            processes.append(js_process)
        if css_process:
            processes.append(css_process)

        if not processes:
            print("Nothing to watch; no entry files found.")
            return

        print("Watching assets. Press Ctrl+C to stop.")
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("Stopping watchers...")
    finally:
        stop_event.set()
        for proc in processes:
            proc.terminate()
        for proc in processes:
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        _write_manifest()


def parse_args(argv: List[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build front-end assets with esbuild.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    build_parser = subparsers.add_parser(
        "build", help="Bundle assets for production or development."
    )
    build_parser.add_argument(
        "--mode",
        choices=("prod", "dev"),
        default="prod",
        help="Build mode. Defaults to 'prod' (minified).",
    )

    watch_parser = subparsers.add_parser(
        "watch", help="Watch entry files and rebuild on change."
    )
    watch_parser.add_argument(
        "--mode",
        choices=("prod", "dev"),
        default="dev",
        help="Watch mode. Defaults to 'dev' (with sourcemaps).",
    )

    subparsers.add_parser("clean", help="Remove generated assets.")

    return parser.parse_args(argv)


def main(argv: List[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])

    try:
        if args.command == "build":
            build(args.mode)
        elif args.command == "watch":
            watch(args.mode)
        elif args.command == "clean":
            _clean_dist()
        else:
            raise BuildError(f"Unknown command: {args.command}")
    except BuildError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
