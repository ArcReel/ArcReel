// Start run-server in official Playwright images with `pnpm e2e:server`, then another terminal runs `pnpm e2e:remote`.
// Container browsers match CI rendering; image versions come from installed @playwright/test and must match.
// Local ports default 3000, overridden by `E2E_SERVER_PORT`, also read by `pnpm e2e:remote`.
import { spawn } from "node:child_process";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const { version } = JSON.parse(readFileSync(require.resolve("@playwright/test/package.json"), "utf8")) as {
  version: string;
};

const hostPort = process.env.E2E_SERVER_PORT ?? "3000";

const args = [
  "run",
  "--rm",
  "--init",
  "--ipc=host",
  // Listen on local loopback only: run-server exposes browser-control interfaces.
  "-p",
  `127.0.0.1:${hostPort}:3000`,
  "--workdir",
  "/home/pwuser",
  "--user",
  "pwuser",
  `mcr.microsoft.com/playwright:v${version}-noble`,
  "/bin/sh",
  "-c",
  `npx -y playwright@${version} run-server --port 3000 --host 0.0.0.0`,
];

const docker = spawn("docker", args, { stdio: "inherit" });
docker.on("exit", (code) => process.exit(code ?? 1));
