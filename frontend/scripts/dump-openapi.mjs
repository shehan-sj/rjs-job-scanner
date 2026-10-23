#!/usr/bin/env node
// Cross-platform launcher for ../scripts/dump_openapi_schema.py — npm's
// built-in script shell (cmd on Windows, sh elsewhere) does not resolve a
// forward-slash-separated relative executable path like
// ".venv/Scripts/python.exe" the same way on both platforms, so this picks
// the repo's venv interpreter (or falls back to whatever `python`/`python3`
// is on PATH) in Node instead of inline shell syntax.
import { spawnSync } from "node:child_process";
import { existsSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const repoRoot = join(here, "..", ".."); // frontend/scripts -> frontend -> repo root

const candidates = [
  join(repoRoot, ".venv", "Scripts", "python.exe"), // Windows venv
  join(repoRoot, ".venv", "bin", "python"), // POSIX venv
];

const python = candidates.find((p) => existsSync(p)) ?? (process.platform === "win32" ? "python" : "python3");

const result = spawnSync(python, [join(repoRoot, "scripts", "dump_openapi_schema.py")], {
  stdio: "inherit",
  cwd: repoRoot,
});

process.exit(result.status ?? 1);
