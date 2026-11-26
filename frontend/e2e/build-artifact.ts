import { createHash } from "node:crypto";
import { execFileSync } from "node:child_process";
import { existsSync, globSync, lstatSync, mkdirSync, readFileSync, realpathSync, writeFileSync } from "node:fs";
import { join, relative, resolve } from "node:path";

const [operation, directory] = process.argv.slice(2);
if (!directory || !process.env.GITHUB_RUN_ID) {
  throw new Error("Expected build-artifact.ts pack|restore DIRECTORY and GITHUB_RUN_ID");
}
const archive = resolve(directory, "browser.tar.gz");
const manifest = join(directory, "build.json");

function digest(value: string | Buffer) {
  return createHash("sha256").update(value).digest("hex");
}

const identity = {
  run: process.env.GITHUB_RUN_ID,
  commit: execFileSync("git", ["rev-parse", "HEAD"], { encoding: "utf8" }).trim(),
  frontendTree: execFileSync("git", ["rev-parse", "HEAD:frontend"], { encoding: "utf8" }).trim(),
  lockfile: digest(readFileSync("pnpm-lock.yaml")),
  environment: digest(JSON.stringify([
    process.env.NODE_ENV ?? "production",
    Object.entries(process.env).filter(([key]) => key.startsWith("VITE_")).sort(),
    ...[".env", ".env.local", ".env.production", ".env.production.local"]
      .filter((file) => existsSync(file)).map((file) => [file, digest(readFileSync(file))]),
  ])),
  runtime: [process.platform, process.arch, process.versions.node.split(".")[0]].join("-"),
};

function requireEntryPoint() {
  if (!existsSync("dist/index.html")) throw new Error("Shared browser build is missing dist/index.html");
}

function browserRuntimePaths() {
  const roots = ["@playwright/test", "@axe-core/playwright", "vite", "@vitejs/plugin-react", "@tailwindcss/vite"]
    .map((name) => join("node_modules", name));
  const packages = new Set<string>();
  function include(link: string) {
    const target = relative("node_modules/.pnpm", realpathSync(link));
    if (target.startsWith("..") || !target.includes("/node_modules/")) {
      throw new Error(`Browser dependency ${link} is outside the pnpm virtual store`);
    }
    const storePath = join("node_modules/.pnpm", target.split("/")[0]);
    if (packages.has(storePath)) return;
    packages.add(storePath);
    for (const dep of globSync(["*", "@*/*"], { cwd: join(storePath, "node_modules") })) {
      const path = join(storePath, "node_modules", dep);
      if (lstatSync(path).isSymbolicLink()) include(path);
    }
  }
  roots.forEach(include);
  return [...roots, ...packages];
}

if (operation === "pack") {
  requireEntryPoint();
  mkdirSync(directory, { recursive: true });
  execFileSync("tar", ["-czf", archive, "dist", ...browserRuntimePaths()], { stdio: "inherit" });
  writeFileSync(manifest, JSON.stringify(identity, null, 2));
} else if (operation === "restore") {
  for (const file of [manifest, archive]) {
    if (!existsSync(file)) throw new Error(`Shared browser build is missing ${file}`);
  }
  const actual = JSON.parse(readFileSync(manifest, "utf8")) as Record<string, unknown>;
  for (const [key, expected] of Object.entries(identity)) {
    if (actual[key] !== expected) {
      throw new Error(`Shared browser build mismatch for ${key}: expected ${expected}, received ${JSON.stringify(actual[key])}`);
    }
  }
  if (existsSync("dist") || existsSync("node_modules")) {
    throw new Error("Restore the shared browser build in a clean frontend checkout");
  }
  execFileSync("tar", ["-xzf", archive], { stdio: "inherit" });
  requireEntryPoint();
} else {
  throw new Error(`Unknown browser build operation: ${operation}`);
}
console.log(`Shared browser build ${operation}: run ${identity.run}, commit ${identity.commit}, tree ${identity.frontendTree}`);
