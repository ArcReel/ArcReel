import { useCallback, useRef, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

import type { SourceFileImpact } from "@/types/episodes-view";

import { ImpactConfirmDialog } from "./ImpactConfirmDialog";

/** 整本源文文件改动命令的响应：上传接口在 `status` 之外还带别的字段，这里只看确认协议需要的部分。 */
export interface SourceFileChangeReply {
  status?: "applied" | "confirmation_required";
  impact?: SourceFileImpact & { text?: string };
  revision?: string;
}

interface PendingConfirmation {
  title: string;
  confirmLabel: string;
  text: string;
  danger: boolean;
  /** 确认时服务端的清单已变，这是更新后的清单。 */
  changed: boolean;
}

/** 清单里有会标 stale、退下或移除的集时按危险操作确认。 */
function isDanger(impact: SourceFileImpact | undefined): boolean {
  if (!impact) return false;
  return impact.changed_with_products.length + impact.retired.length + impact.removed.length > 0;
}

/**
 * 整本源文文件的插入、替换、编辑、删除与调序共用的确认流程。
 *
 * `run(title, confirmLabel, call)` 先以 `call(null)` 提交：没有受影响的集时服务端直接执行。需要确认时，确认框只呈现
 * 服务端成文的受影响集清单；确认后带上清单的 `revision` 再提交，清单在此期间变了时换成新清单再确认一次。
 * 返回执行后的响应，创作者取消时返回 null。请求出错时关闭确认框并把错误抛给调用方。
 *
 * 每次提交期间 `busy` 为 true，等待创作者确认期间为 false。上一次 `run` 尚未结束时再调用直接返回 null，不发请求。
 */
export function useSourceFileChange() {
  const { t } = useTranslation("dashboard");
  const [pending, setPending] = useState<PendingConfirmation | null>(null);
  const [busy, setBusy] = useState(false);
  const answer = useRef<((confirmed: boolean) => void) | null>(null);
  const running = useRef(false);

  const ask = useCallback(
    (next: PendingConfirmation) =>
      new Promise<boolean>((resolve) => {
        answer.current = resolve;
        setPending(next);
      }),
    [],
  );

  const run = useCallback(
    async <R extends SourceFileChangeReply>(
      title: string,
      confirmLabel: string,
      call: (revision: string | null) => Promise<R>,
    ): Promise<R | null> => {
      if (running.current) return null;
      running.current = true;
      const submit = async (revision: string | null) => {
        setBusy(true);
        try {
          return await call(revision);
        } finally {
          setBusy(false);
        }
      };
      try {
        let reply = await submit(null);
        let changed = false;
        while (reply.status === "confirmation_required") {
          const confirmed = await ask({
            title,
            confirmLabel,
            text: reply.impact?.text ?? "",
            danger: isDanger(reply.impact),
            changed,
          });
          if (!confirmed) return null;
          reply = await submit(reply.revision ?? null);
          changed = true;
        }
        return reply;
      } finally {
        running.current = false;
        answer.current = null;
        setPending(null);
      }
    },
    [ask],
  );

  const respond = (confirmed: boolean) => answer.current?.(confirmed);

  const dialog: ReactNode = (
    <ImpactConfirmDialog
      request={
        pending === null
          ? null
          : {
              title: pending.title,
              body: (
                <div className="flex flex-col gap-2">
                  {pending.changed ? <p className="text-warn">{t("source_file_change_changed")}</p> : null}
                  <p>{t("source_file_change_impact")}</p>
                  <p className="whitespace-pre-line">{pending.text}</p>
                </div>
              ),
              confirmLabel: pending.confirmLabel,
              runningLabel: t("source_file_change_running"),
              destructive: pending.danger,
            }
      }
      busy={busy}
      onConfirm={() => respond(true)}
      onCancel={() => respond(false)}
    />
  );

  return { run, busy, dialog };
}
