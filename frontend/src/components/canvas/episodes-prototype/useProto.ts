// PROTOTYPE（#2831）：内存状态 + 模拟服务端逐窗串联的计时器。
// 文件操作先用纯函数算出下一状态、给确认对话框列影响，确认后再 commit。
import { useCallback, useEffect, useState } from "react";
import * as M from "./model";

const TICK_MS = 2000;

export function useProto() {
  const [s, setS] = useState<M.ProtoState>(M.initialState);

  useEffect(() => {
    if (!s.planning) return;
    const t = setTimeout(() => setS((p) => M.planOneWindow(p)), TICK_MS);
    return () => clearTimeout(t);
  }, [s.planning]);

  useEffect(() => {
    if (s.candidate?.status !== "generating") return;
    const t = setTimeout(() => setS((p) => M.candidateStep(p)), TICK_MS);
    return () => clearTimeout(t);
  }, [s.candidate?.status, s.candidate?.reached]);

  const run = useCallback(<A extends unknown[]>(fn: (s: M.ProtoState, ...a: A) => M.ProtoState) =>
    (...a: A) => setS((p) => fn(p, ...a)), []);

  return {
    s,
    commit: (next: M.ProtoState) => setS(next),
    reset: () => setS(M.initialState()),
    beginPlanning: run(M.beginPlanning),
    beginGapPlanning: run(M.beginGapPlanning),
    stopPlanning: run(M.stopPlanning),
    moveBoundary: run(M.moveBoundary),
    cutAt: run(M.cutAt),
    splitEp: run(M.splitEp),
    mergeWithNext: run(M.mergeWithNext),
    removeCutsAfter: run(M.removeCutsAfter),
    addOwnEpisodes: run(M.addOwnEpisodes),
    addBlankEpisode: run(M.addBlankEpisode),
    simulateExternal: run(M.simulateExternal),
    beginReplan: run(M.beginReplan),
    stopCandidate: run(M.stopCandidate),
    adoptCandidate: run(M.adoptCandidate),
    discardCandidate: run(M.discardCandidate),
  };
}

export type Proto = ReturnType<typeof useProto>;
