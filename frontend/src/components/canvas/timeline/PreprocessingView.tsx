import { useState, useEffect, useCallback, useId, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Edit3, Save, X } from "lucide-react";
import { API } from "@/api";
import { voidPromise } from "@/utils/async";
import { useAppStore } from "@/stores/app-store";
import { StreamMarkdown } from "@/components/copilot/StreamMarkdown";

/** Editing 控制 + 状态，`renderToolbar` 用这个接口把 toolbar 渲染抬升给调用方。 */
export interface PreprocessingToolbarContext {
  editing: boolean;
  saving: boolean;
  startEdit: () => void;
  save: () => void;
  cancel: () => void;
}

interface PreprocessingViewProps {
  projectName: string;
  episode: number;
  contentMode: "narration" | "drama" | "reference_video";
  /**
   * 紧凑模式：隐藏"● {statusLabel}"辅助行（当上层已显示同等语义的 page header 时避免重复），
   * 并用更克制的 markdown typography（h1/h2 字号下调、去除 h1 下划线）。
   */
  compact?: boolean;
  /**
   * 可选 render prop：把 edit/save/cancel 控件抬到调用方（比如页面 header 右侧），
   * 组件内部就不再渲染默认 toolbar 行。narration/drama 不传走默认，行为不变。
   */
  renderToolbar?: (ctx: PreprocessingToolbarContext) => ReactNode;
}

export function PreprocessingView({
  projectName,
  episode,
  contentMode,
  compact = false,
  renderToolbar,
}: PreprocessingViewProps) {
  const { t } = useTranslation(["dashboard", "common"]);
  const pushToast = useAppStore((s) => s.pushToast);
  const draftRevisionKey = `draft:episode_${episode}_step1`;
  const draftRevision = useAppStore((s) => s.getEntityRevision(draftRevisionKey));
  const [content, setContent] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [editing, setEditing] = useState(false);
  const [editContent, setEditContent] = useState("");
  const [saving, setSaving] = useState(false);
  const statusLabelId = useId();

  useEffect(() => {
    let cancelled = false;
    // 首次加载或切换草稿时展示加载状态并重置编辑态，再触发异步 fetch
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (!content) setLoading(true);
    setEditing(false);

    API.getDraftContent(projectName, episode, 1)
      .then((text) => {
        if (!cancelled) {
          setContent(text);
          setEditContent(text);
        }
      })
      .catch(() => {
        if (!cancelled) setContent(null);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- content 仅用于决定是否显示加载态，加入 deps 会在内容更新后触发重新拉取，导致循环
  }, [projectName, episode, draftRevision]);

  const handleSave = useCallback(async () => {
    setSaving(true);
    try {
      await API.saveDraft(projectName, episode, 1, editContent);
      setContent(editContent);
      setEditing(false);
      pushToast(t("dashboard:preprocessing_saved"), "success");
    } catch {
      pushToast(t("dashboard:save_failed"), "error");
    } finally {
      setSaving(false);
    }
  }, [projectName, episode, editContent, pushToast, t]);

  const cancelEdit = useCallback(() => {
    setEditing(false);
    setEditContent(content ?? "");
  }, [content]);

  const statusLabel =
    contentMode === "narration"
      ? t("dashboard:segment_split_complete")
      : contentMode === "drama"
        ? t("dashboard:script_normalized_complete")
        : t("dashboard:reference_units_split_complete_label");

  if (loading) {
    return (
      <div className="flex h-64 items-center justify-center text-gray-500">
        {t("dashboard:loading_preprocessing")}
      </div>
    );
  }

  if (content === null) {
    return (
      <div className="flex h-64 items-center justify-center text-gray-500">
        {t("dashboard:no_preprocessing_content")}
      </div>
    );
  }

  // 当调用方接管 toolbar 时，仍把 statusLabel 以 sr-only 形式保留，供 textarea 的 aria-labelledby 引用
  // （保持 a11y 结构稳定）。内置 toolbar 仅在没有 renderToolbar 时渲染。
  const defaultToolbar = (
    <div className="flex items-center justify-between">
      {compact ? (
        <span id={statusLabelId} className="sr-only">{statusLabel}</span>
      ) : (
        <div className="flex items-center gap-2">
          <div className="h-1.5 w-1.5 rounded-full bg-emerald-500" />
          <span id={statusLabelId} className="text-xs text-gray-500">{statusLabel}</span>
        </div>
      )}
      <div className="flex items-center gap-1">
        {editing ? (
          <>
            <button
              type="button"
              onClick={voidPromise(handleSave)}
              disabled={saving}
              className="flex items-center gap-1 rounded px-2 py-1 text-xs text-green-400 transition-colors hover:bg-gray-800 disabled:opacity-50"
            >
              <Save className="h-3.5 w-3.5" />
              {saving ? t("common:saving") : t("common:save")}
            </button>
            <button
              type="button"
              onClick={cancelEdit}
              className="flex items-center gap-1 rounded px-2 py-1 text-xs text-gray-400 transition-colors hover:bg-gray-800"
            >
              <X className="h-3.5 w-3.5" />
              {t("common:cancel")}
            </button>
          </>
        ) : (
          <button
            type="button"
            onClick={() => setEditing(true)}
            className="flex items-center gap-1 rounded px-2 py-1 text-xs text-gray-400 transition-colors hover:bg-gray-800 hover:text-gray-200"
          >
            <Edit3 className="h-3.5 w-3.5" />
            {t("common:edit")}
          </button>
        )}
      </div>
    </div>
  );

  return (
    <div className="flex flex-col gap-3">
      {renderToolbar ? (
        <>
          <span id={statusLabelId} className="sr-only">{statusLabel}</span>
          {renderToolbar({
            editing,
            saving,
            startEdit: () => setEditing(true),
            save: voidPromise(handleSave),
            cancel: cancelEdit,
          })}
        </>
      ) : (
        defaultToolbar
      )}

      {/* Content */}
      {editing ? (
        <textarea
          aria-labelledby={statusLabelId}
          value={editContent}
          onChange={(e) => setEditContent(e.target.value)}
          className="min-h-[400px] w-full resize-y rounded-lg border border-gray-700 bg-gray-800 p-4 font-mono text-sm leading-relaxed text-gray-200 outline-none focus-ring focus-visible:border-indigo-500"
        />
      ) : (
        <div
          className={`prose-invert max-w-none overflow-x-auto rounded-lg border border-gray-800 bg-gray-900/50 p-4 text-sm ${compact ? "markdown-compact" : ""}`}
        >
          <StreamMarkdown content={content} />
        </div>
      )}
    </div>
  );
}
