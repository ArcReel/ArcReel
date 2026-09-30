// PROTOTYPE（#2831）：内存状态 + 模拟服务端逐窗串联的计时器。
// 文件操作先用纯函数算出下一状态、给确认对话框列影响，确认后再 commit。
import { useCallback, useEffect, useState } from "react";
import * as M from "./model";

const TICK_MS = 2000;

export function useProto() {
  const [s, setS] = useState<M.ProtoState>(M.initialState);
  const [base, setBase] = useState<M.ProtoState | null>(null);

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

  const load = useCallback((st: M.ProtoState) => { setBase(st); setS(st); }, []);

  return {
    s,
    source: base ? "real" as const : "sample" as const,
    commit: (next: M.ProtoState) => setS(next),
    /** 装载真实项目的初始状态；重置时回到它 */
    load,
    reset: () => setS(base ?? M.initialState()),
    loadSample: () => { setBase(null); setS(M.initialState()); },
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
    continueCandidate: run(M.continueCandidate),
    deleteEp: run(M.deleteEp),
    adoptCandidate: run(M.adoptCandidate),
    discardCandidate: run(M.discardCandidate),
  };
}

export type Proto = ReturnType<typeof useProto>;
