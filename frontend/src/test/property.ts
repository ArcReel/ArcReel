import * as fc from "fast-check";

const profile = process.env.FC_PROFILE ?? "ci";
if (profile !== "ci" && profile !== "nightly") throw new Error(`Unknown FC_PROFILE: ${profile}`);

export const propertyTimeout = profile === "nightly" ? 120_000 : undefined;
export const maxSteps = profile === "nightly" ? 80 : 16;
export const commandLimits = { maxCommands: maxSteps, size: "max" as const, replayPath: process.env.FC_REPLAY_PATH || undefined };

export function propertyParameters<T>(file: string, testName?: string): fc.Parameters<T> {
  const path = process.env.FC_PATH;
  const filter = testName ? ` -t '${testName}'` : "";
  return {
    numRuns: path ? 1 : profile === "nightly" ? 200 : 25,
    seed: process.env.FC_SEED ? Number(process.env.FC_SEED) : profile === "ci" ? 4161 : undefined,
    path,
    endOnFailure: Boolean(path),
    reporter(result) {
      if (!result.failed) return;
      const replayPath = /replayPath="([^"]*)"/.exec(String(result.counterexample))?.[1] ?? "";
      const command = `FC_PROFILE=${profile} FC_SEED=${result.seed} FC_PATH='${result.counterexamplePath}' FC_REPLAY_PATH='${replayPath}' pnpm --dir frontend exec vitest run ${file}${filter}`;
      throw new Error(`${fc.defaultReportMessage(result)}\n${command}`, { cause: result.errorInstance });
    },
  };
}
