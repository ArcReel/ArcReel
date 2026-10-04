// 在官方 Playwright 镜像里启动 run-server：`pnpm e2e:server`，再在另一个终端运行 `pnpm e2e:remote`。
// 浏览器在容器里渲染，结果与 CI 一致；镜像版本取自已安装的 @playwright/test，两者必须一致。
import { spawn } from "node:child_process";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const { version } = JSON.parse(readFileSync(require.resolve("@playwright/test/package.json"), "utf8")) as {
  version: string;
};

const args = [
  "run",
  "--rm",
  "--init",
  "--ipc=host",
  // 只监听本机回环：run-server 对外暴露的是浏览器控制接口。
  "-p",
  "127.0.0.1:3000:3000",
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
