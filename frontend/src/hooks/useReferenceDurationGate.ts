import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { API, ReferenceProjectionError, SpeechAdmissionError } from "@/api";
import { useAppStore } from "@/stores/app-store";
import { errMsg } from "@/utils/async";
import type { DurationConfirmItem } from "@/components/canvas/reference/ReferenceDurationConfirmDialog";
import type { ReferenceRequestOptions } from "@/types";

interface Options {
  projectName: string;
  episode: number;
  /** 由旁白工作流提供；预检与确认后的入队必须复用同一组选项。 */
  requestOptions?: ReferenceRequestOptions;
}

/**
 * 该 unit 此刻是否仍该入队，由调用方按当前入口的语义提供，须实时读取而非渲染期快照：
 * 预检与用户思考都是 await 窗口，其间同一 unit 可能已被其它入口或智能体占用。
 *
 * 判定按入口而非按闸门统一——批量入口的作用对象是「还没有成片的单元」，弹窗停留期间
 * 完成的要剔除（任务完成后它不再 busy，队列去重也只看 queued/running/cancelling，
 * 放过去就是第二次计费并覆盖刚出的成片）；而单元入口的「重新生成」本就要覆盖已有成片，
 * 同一条判定会把它一并挡掉。
 */
type CanEnqueue = (unitId: string) => boolean;

interface PendingConfirm {
  items: DurationConfirmItem[];
  /** 通过确认的全部单元（含无需确认的），确认后按原顺序入队 */
  unitIds: string[];
}

type ConfirmedDurations = ReadonlyMap<string, number>;
type Commit = (unitIds: string[], confirmedDurations: ConfirmedDurations) => Promise<void>;

/**
 * 参考生视频入口的时长确认闸门：入队前预检取档，申请秒数与请求时长基准不一致时先让
 * 用户确认，取消则一个都不入队。
 *
 * 批量入口聚合成一次确认（逐个弹窗会让用户为一次操作点 N 遍），单入口与批量入口共用
 * 同一条闸门——否则批量按钮会成为绕过确认的旁路。
 */
export function useReferenceDurationGate({ projectName, episode, requestOptions }: Options) {
  const { t } = useTranslation("dashboard");
  const [pending, setPending] = useState<PendingConfirm | null>(null);
  // 入队回调随 run 一起捕获：确认发生在 run 之后的任意时刻，不能从渲染期闭包重取
  const commitRef = useRef<Commit | null>(null);
  // 复核判定同理随 run 捕获：它按入口语义而定，确认时刻要用的是发起这一轮的那个入口的
  const canEnqueueRef = useRef<CanEnqueue | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  // 项目/剧集切换即作废在途预检与未决弹窗：二者都绑定切换前那一份数据，留到切换后
  // 确认就会把上一个剧集的单元入队。
  useEffect(() => {
    return () => {
      abortRef.current?.abort();
      abortRef.current = null;
      setPending(null);
      commitRef.current = null;
      canEnqueueRef.current = null;
    };
  }, [projectName, episode]);

  const run = useCallback(
    async (
      unitIds: string[],
      commit: Commit,
      canEnqueue: CanEnqueue,
    ) => {
      if (unitIds.length === 0) return;
      // 接管方轮换 controller：新一轮预检作废前任，未决的确认弹窗随之被新一轮替换。
      // 弹窗与 commit 必须在此刻一并清掉，不能等新一轮的结果来覆盖——新一轮若全部无需
      // 确认（直接 commit 返回），旧弹窗会留在屏幕上，用户点确认就按上一轮的清单入队。
      abortRef.current?.abort();
      setPending(null);
      commitRef.current = null;
      canEnqueueRef.current = null;
      const controller = new AbortController();
      abortRef.current = controller;
      const { signal } = controller;

      const results = await Promise.all(
        unitIds.map(async (unitId) => {
          try {
            const precheck = await API.precheckReferenceVideoDuration(
              projectName,
              episode,
              unitId,
              { ...requestOptions, signal },
            );
            return { unitId, precheck };
          } catch (e) {
            if (signal.aborted) return null;
            return { unitId, error: e };
          }
        }),
      );
      if (signal.aborted) return;

      const ok: DurationConfirmItem[] = [];
      let failed = 0;
      const admissionMessages = new Set<string>();
      for (const result of results) {
        if (!result) continue;
        if ("error" in result) {
          if (result.error instanceof SpeechAdmissionError || result.error instanceof ReferenceProjectionError) {
            admissionMessages.add(result.error.message);
          } else {
            failed += 1;
          }
          continue;
        }
        ok.push(result);
      }
      // 预检失败的单元不入队：无从判断成片时长是否与请求基准一致，静默按档位生成会烧掉配额
      if (admissionMessages.size > 0) {
        useAppStore.getState().pushToast(Array.from(admissionMessages).join("\n"), "error");
      }
      if (failed > 0) {
        useAppStore
          .getState()
          .pushToast(t("reference_duration_precheck_failed", { count: failed }), "error");
      }
      if (ok.length === 0) return;

      // 弹窗打开时刻复核：预检这段 await 窗口里同一 unit 可能已被别处入队或已生成完成，
      // 把它列进确认清单等于请用户为一件做不成（或不该再做）的事拍板。
      // 静默跳过而非报错——与批量入口「把还能生成的都排上」同口径。
      const available = ok.filter((item) => canEnqueue(item.unitId));
      if (available.length === 0) return;

      const needsConfirmation = available.filter((item) => item.precheck.needs_confirmation);
      const passing = available.map((item) => item.unitId);
      if (needsConfirmation.length === 0) {
        await commit(passing, new Map());
        return;
      }
      commitRef.current = commit;
      canEnqueueRef.current = canEnqueue;
      setPending({ items: needsConfirmation, unitIds: passing });
    },
    [projectName, episode, requestOptions, t],
  );

  const confirm = useCallback(() => {
    const current = pending;
    const commit = commitRef.current;
    const canEnqueue = canEnqueueRef.current;
    setPending(null);
    commitRef.current = null;
    canEnqueueRef.current = null;
    if (!current || !commit) return;
    // 提交时刻再复核一次：弹窗停留期间（用户思考时长，可以很长）清单里的单元可能已被
    // 别处入队，批量场景下还可能已经生成完成——按冻结清单原样提交会重复计费。
    const targets = canEnqueue ? current.unitIds.filter(canEnqueue) : current.unitIds;
    if (targets.length === 0) return;
    // commit 自身已按入口口径提示失败，这里只兜住漏出的意外异常
    const confirmedDurations = new Map(
      current.items.map((item) => [item.unitId, item.precheck.request_duration]),
    );
    void commit(targets, confirmedDurations).catch((e: unknown) => {
      useAppStore
        .getState()
        .pushToast(t("reference_generate_request_failed", { error: errMsg(e) }), "error");
    });
  }, [pending, t]);

  const cancel = useCallback(() => {
    setPending(null);
    commitRef.current = null;
    canEnqueueRef.current = null;
  }, []);

  return {
    run,
    /** 直接摊给 ReferenceDurationConfirmDialog */
    dialogProps: {
      open: pending !== null,
      items: pending?.items ?? [],
      onConfirm: confirm,
      onCancel: cancel,
    },
  };
}
