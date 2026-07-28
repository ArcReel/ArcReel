import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { API } from "@/api";
import { useAppStore } from "@/stores/app-store";
import { errMsg } from "@/utils/async";
import type { DurationConfirmItem } from "@/components/canvas/reference/ReferenceDurationConfirmDialog";

interface Options {
  projectName: string;
  episode: number;
}

interface PendingConfirm {
  items: DurationConfirmItem[];
  /** 通过确认的全部单元（含无需确认的），确认后按原顺序入队 */
  unitIds: string[];
}

/**
 * 参考视频生成入口的时长确认闸门：入队前预检取档，申请秒数与剧本编排不一致时先让
 * 用户确认，取消则一个都不入队。
 *
 * 批量入口聚合成一次确认（逐个弹窗会让用户为一次操作点 N 遍），单入口与批量入口共用
 * 同一条闸门——否则批量按钮会成为绕过确认的旁路。
 */
export function useReferenceDurationGate({ projectName, episode }: Options) {
  const { t } = useTranslation("dashboard");
  const [pending, setPending] = useState<PendingConfirm | null>(null);
  // 入队回调随 run 一起捕获：确认发生在 run 之后的任意时刻，不能从渲染期闭包重取
  const commitRef = useRef<((unitIds: string[]) => Promise<void>) | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  // 项目/剧集切换即作废在途预检与未决弹窗：二者都绑定切换前那一份数据，留到切换后
  // 确认就会把上一个剧集的单元入队。
  useEffect(() => {
    return () => {
      abortRef.current?.abort();
      abortRef.current = null;
      setPending(null);
      commitRef.current = null;
    };
  }, [projectName, episode]);

  const run = useCallback(
    async (unitIds: string[], commit: (unitIds: string[]) => Promise<void>) => {
      if (unitIds.length === 0) return;
      // 接管方轮换 controller：新一轮预检作废前任，未决的确认弹窗随之被新一轮替换。
      // 弹窗与 commit 必须在此刻一并清掉，不能等新一轮的结果来覆盖——新一轮若全部无需
      // 确认（直接 commit 返回），旧弹窗会留在屏幕上，用户点确认就按上一轮的清单入队。
      abortRef.current?.abort();
      setPending(null);
      commitRef.current = null;
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
              { signal },
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
      for (const result of results) {
        if (!result) continue;
        if ("error" in result) {
          failed += 1;
          continue;
        }
        ok.push(result);
      }
      // 预检失败的单元不入队：无从判断成片时长是否与剧本一致，静默按档位生成会烧掉配额
      if (failed > 0) {
        useAppStore
          .getState()
          .pushToast(t("reference_duration_precheck_failed", { count: failed }), "error");
      }
      if (ok.length === 0) return;

      const needsConfirmation = ok.filter((item) => item.precheck.needs_confirmation);
      const passing = ok.map((item) => item.unitId);
      if (needsConfirmation.length === 0) {
        await commit(passing);
        return;
      }
      commitRef.current = commit;
      setPending({ items: needsConfirmation, unitIds: passing });
    },
    [projectName, episode, t],
  );

  const confirm = useCallback(() => {
    const current = pending;
    const commit = commitRef.current;
    setPending(null);
    commitRef.current = null;
    if (!current || !commit) return;
    // commit 自身已按入口口径提示失败，这里只兜住漏出的意外异常
    void commit(current.unitIds).catch((e: unknown) => {
      useAppStore
        .getState()
        .pushToast(t("reference_generate_request_failed", { error: errMsg(e) }), "error");
    });
  }, [pending, t]);

  const cancel = useCallback(() => {
    setPending(null);
    commitRef.current = null;
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
