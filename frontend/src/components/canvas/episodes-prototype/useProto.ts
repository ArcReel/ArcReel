// PROTOTYPE（#2767）：内存状态 + 模拟服务端逐窗串联的计时器。
import { useCallback, useEffect, useState } from "react";
import * as M from "./model";

/** 模拟一批（一次非流式调用）的耗时；真实耗时以分钟计 */
const TICK_MS = 2500;

export function useProto() {
  const [s, setS] = useState<M.ProtoState>(M.initialState);

  useEffect(() => {
    if (!s.planning) return;
    const t = setTimeout(() => setS((p) => M.planOneWindow(p)), TICK_MS);
    return () => clearTimeout(t);
  }, [s.planning, s.cursor]);

  useEffect(() => {
    if (s.candidate?.status !== "generating") return;
    const t = setTimeout(() => setS((p) => M.candidateStep(p)), TICK_MS);
    return () => clearTimeout(t);
  }, [s.candidate?.status, s.candidate?.reached]);

  const run = useCallback(<A extends unknown[]>(fn: (s: M.ProtoState, ...a: A) => M.ProtoState) =>
    (...a: A) => setS((p) => fn(p, ...a)), []);

  return {
    s,
    reset: () => setS(M.initialState()),
    beginPlanning: run(M.beginPlanning),
    stopPlanning: run(M.stopPlanning),
    moveBoundary: run(M.moveBoundary),
    manualCut: run(M.manualCut),
    splitEp: run(M.splitEp),
    mergeWithNext: run(M.mergeWithNext),
    removeCutsAfter: run(M.removeCutsAfter),
    addOwnEpisodes: run(M.addOwnEpisodes),
    addBlankEpisode: run(M.addBlankEpisode),
    replaceSource: run(M.replaceSource),
    renameEp: run(M.renameEp),
    beginReplan: run(M.beginReplan),
    stopCandidate: run(M.stopCandidate),
    adoptCandidate: run(M.adoptCandidate),
    discardCandidate: run(M.discardCandidate),
  };
}

export type Proto = ReturnType<typeof useProto>;
