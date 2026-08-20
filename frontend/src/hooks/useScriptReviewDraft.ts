import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { API } from "@/api";
import type { ScriptReviewState } from "@/types";
import { useAppStore } from "@/stores/app-store";

/** 面板可编辑的草稿：`ScriptReviewState.content` 的某个变体。 */
type ScriptReviewContent = NonNullable<ScriptReviewState["content"]>;

function scriptReviewErrorMessage(err: unknown): string {
  return err instanceof Error ? err.message : "";
}

/** 内容是否有未保存编辑：以序列化比对，draft 由 server content 克隆而来，键序稳定。 */
function isDirty(draft: unknown, serverContent: unknown): boolean {
  if (draft == null) return false;
  return JSON.stringify(draft) !== JSON.stringify(serverContent);
}

function clone<TDraft>(content: TDraft | null): TDraft | null {
  return content ? (JSON.parse(JSON.stringify(content)) as TDraft) : null;
}

interface ScriptReviewDraftOptions<TDraft extends ScriptReviewContent> {
  projectName: string;
  episode: number;
  /**
   * 从服务端审核态取出本面板可编辑的那份内容；形状不属于本面板的变体时返回 null。
   * 须是稳定引用（模块级函数或 `useCallback`）——它参与拉取 effect 的依赖。
   */
  selectContent: (state: ScriptReviewState) => TDraft | null;
  /** 确认成功后的面板专属处理（含成功提示）；失败提示由本 hook 统一给出。 */
  onConfirmed: () => void;
}

interface ScriptReviewDraftHandle<TDraft extends ScriptReviewContent> {
  /** 最近一次拉取到的服务端审核态；尚未拿到任何响应时为 null。 */
  state: ScriptReviewState | null;
  draft: TDraft | null;
  setDraft: React.Dispatch<React.SetStateAction<TDraft | null>>;
  /** 草稿与其所基于的服务端内容不一致，即有未保存编辑。 */
  dirty: boolean;
  loading: boolean;
  loadError: { message: string } | null;
  saving: boolean;
  confirming: boolean;
  /** 保存 / 确认请求在途：调用方据此锁住编辑控件，避免回显覆盖请求发出后的新编辑。 */
  busy: boolean;
  retry: () => void;
  save: () => Promise<void>;
  confirm: () => Promise<void>;
}

/**
 * step1 → step2 内容确认面板的草稿状态机：拉取 / 外部刷新 / 脏标记 / 采纳 / 保存 / 确认。
 * `ScriptReviewGate`（drama、narration）与 `ReferenceStep1PreviewPanel`（reference_video）
 * 共用，面板只保留各自的呈现与专属交互。
 */
export function useScriptReviewDraft<TDraft extends ScriptReviewContent>({
  projectName,
  episode,
  selectContent,
  onConfirmed,
}: ScriptReviewDraftOptions<TDraft>): ScriptReviewDraftHandle<TDraft> {
  const { t } = useTranslation("dashboard");
  const pushToast = useAppStore((s) => s.pushToast);
  const draftRevision = useAppStore((s) => s.getEntityRevision(`draft:episode_${episode}_step1`));

  const [state, setState] = useState<ScriptReviewState | null>(null);
  const [draft, setDraft] = useState<TDraft | null>(null);
  // 保存时提交的基线指纹：绑定在**当前草稿所基于的那份服务端内容**上，只在草稿采用服务端内容时推进。
  // 不能改用 state.fingerprint——有未保存编辑时的外部刷新会更新 state 却保留旧草稿，届时提交
  // state.fingerprint 等于拿别人新写的内容当基线，OCC 会放行并静默覆盖对方的修改。
  const [baseFingerprint, setBaseFingerprint] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<{ message: string } | null>(null);
  const [reloadNonce, setReloadNonce] = useState(0);
  const [saving, setSaving] = useState(false);
  const [confirming, setConfirming] = useState(false);

  const serverContent = state ? selectContent(state) : null;
  const dirty = useMemo(() => isDirty(draft, serverContent), [draft, serverContent]);
  const busy = saving || confirming;

  // 把 dirty 镜像进 ref，供下方拉取 effect 读取最新值，而无需把 dirty 列入 deps（否则每次编辑都会重新拉取）。
  const dirtyRef = useRef(false);
  useEffect(() => {
    dirtyRef.current = dirty;
  }, [dirty]);

  // 在途拉取的 controller 与在途标记，供 adopt 作废与补拉判定。
  const loadControllerRef = useRef<AbortController | null>(null);
  const loadInFlightRef = useRef(false);

  // 采用服务端内容为新草稿（深克隆，避免与服务端态共享引用）。用户主动动作（保存 / 确认）后调用，
  // 总是覆盖本地草稿；setDraft 传值而非更新器，保持纯净、不在更新器内读写 ref。
  //
  // 采纳前发出的拉取一律作废：它读到的服务端态未必包含本次写入，回来时 dirtyRef 已因采纳而为
  // false，会按旧内容回写 draft 与 baseFingerprint——草稿回退，且下次保存拿旧指纹撞 OCC。
  // 但作废掉的若是外部事件（Agent 改 step1 的 revision）触发的那次拉取，该 revision 已被消费、
  // 不会再有请求补上，外部更新就此丢失；故确有在途拉取被作废时补发一轮，把它重新拉回来。
  const adopt = useCallback(
    (next: ScriptReviewState) => {
      const abandonedLoad = loadInFlightRef.current;
      loadControllerRef.current?.abort();
      loadInFlightRef.current = false;
      setState(next);
      setDraft(clone(selectContent(next)));
      setBaseFingerprint(next.fingerprint);
      if (abandonedLoad) setReloadNonce((n) => n + 1);
    },
    [selectContent],
  );

  const retry = useCallback(() => {
    setLoadError(null);
    setLoading(true);
    setReloadNonce((n) => n + 1);
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    const { signal } = controller;
    loadControllerRef.current = controller;
    loadInFlightRef.current = true;
    // 已拿到过任一响应（空态或内容态）后，revision 触发的重新拉取静默刷新、不闪加载态；
    const hadResponse = state != null;
    // 屏上有真实内容可保留时，刷新失败静默保留、不破坏用户视图；无内容（首屏，或空态）时失败才进错误态。
    const hasContent = draft != null;
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (!hadResponse) setLoading(true);
    API.getScriptReview(projectName, episode, { signal })
      .then((next) => {
        // 响应已 resolve 之后才 abort 的窗口：写 state 前复核，避免离场的这轮回写。
        if (signal.aborted) return;
        setLoadError(null);
        setState(next);
        // 外部刷新（挂载 / Agent 改 step1 触发的 revision）：用户无未保存编辑时采用服务端内容，
        // 有编辑则仅更新服务端态、保留用户草稿。dirtyRef 读取在 effect 内安全（非 render 期）。
        if (!dirtyRef.current) {
          setDraft(clone(selectContent(next)));
          setBaseFingerprint(next.fingerprint);
        }
      })
      .catch((err: unknown) => {
        if (signal.aborted) return;
        // 屏上无真实内容（首屏失败，或空态后 revision 刷新失败）→ 错误态（区别于空态）+ 重试；
        // 已有内容的静默刷新失败则保留现有内容，不破坏用户视图。
        if (!hasContent) setLoadError({ message: scriptReviewErrorMessage(err) });
      })
      .finally(() => {
        // 收尾让位给接管方：已作废就不碰在途标记与 loading，否则会打断新一轮加载态。
        if (!signal.aborted) {
          loadInFlightRef.current = false;
          setLoading(false);
        }
      });
    return () => {
      controller.abort();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- state/draft 仅用于决定加载态与错误态分支，加入 deps 会在每次刷新后重新拉取造成循环
  }, [projectName, episode, draftRevision, reloadNonce, selectContent]);

  const save = useCallback(async () => {
    if (!draft) return;
    setSaving(true);
    try {
      adopt(await API.saveScriptReviewContent(projectName, episode, draft, baseFingerprint));
      pushToast(t("dashboard:review_saved"), "success");
    } catch (err) {
      pushToast(scriptReviewErrorMessage(err) || t("dashboard:save_failed", { message: "" }), "error");
    } finally {
      setSaving(false);
    }
  }, [draft, baseFingerprint, projectName, episode, adopt, pushToast, t]);

  const confirm = useCallback(async () => {
    setConfirming(true);
    try {
      if (dirty && draft) {
        adopt(await API.saveScriptReviewContent(projectName, episode, draft, baseFingerprint));
      }
      adopt(await API.confirmScriptReview(projectName, episode));
      onConfirmed();
    } catch (err) {
      pushToast(scriptReviewErrorMessage(err) || t("dashboard:review_confirm_failed"), "error");
    } finally {
      setConfirming(false);
    }
  }, [dirty, draft, baseFingerprint, projectName, episode, adopt, onConfirmed, pushToast, t]);

  return {
    state,
    draft,
    setDraft,
    dirty,
    loading,
    loadError,
    saving,
    confirming,
    busy,
    retry,
    save,
    confirm,
  };
}
