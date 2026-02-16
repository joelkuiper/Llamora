import { build } from "esbuild";
import { mkdir, copyFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import path from "node:path";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const projectRoot = path.resolve(__dirname, "..");
const vendorDir = path.join(projectRoot, "frontend", "static", "js", "vendor");

const bundles = [
  {
    name: "markdown-it",
    entry: "markdown-it",
    outfile: "markdown-it.min.js",
    globalName: "MarkdownIt",
  },
  {
    name: "markdown-it-task-lists",
    entry: "markdown-it-task-lists",
    outfile: "markdown-it-task-lists.min.js",
    globalName: "markdownitTaskLists",
  },
  {
    name: "floating-ui",
    entry: "@floating-ui/dom",
    outfile: "floating-ui.min.js",
    globalName: "FloatingUIDOM",
  },
];

const copies = [
  {
    name: "htmx-ext-sse",
    from: path.join(projectRoot, "node_modules", "htmx-ext-sse", "sse.js"),
    outfile: "htmx-ext-sse.js",
  },
  {
    name: "htmx-ext-response-targets",
    from: path.join(
      projectRoot,
      "node_modules",
      "htmx-ext-response-targets",
      "response-targets.js",
    ),
    outfile: "htmx-ext-response-targets.js",
  },
  {
    name: "htmx",
    from: path.join(projectRoot, "node_modules", "htmx.org", "dist", "htmx.min.js"),
    outfile: "htmx.min.js",
  },
  {
    name: "dompurify",
    from: path.join(projectRoot, "node_modules", "dompurify", "dist", "purify.min.js"),
    outfile: "purify.min.js",
  },
];

async function run() {
  await mkdir(vendorDir, { recursive: true });

  for (const bundle of bundles) {
    await build({
      entryPoints: [bundle.entry],
      outfile: path.join(vendorDir, bundle.outfile),
      bundle: true,
      minify: true,
      format: "iife",
      globalName: bundle.globalName,
      target: "es2018",
      logLevel: "info",
    });
  }

  for (const copy of copies) {
    await copyFile(copy.from, path.join(vendorDir, copy.outfile));
  }
}

run().catch((error) => {
  console.error(error);
  process.exit(1);
});
