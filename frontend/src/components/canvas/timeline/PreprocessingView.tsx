import { useState, useEffect, useCallback, useId } from "react";
import { useTranslation } from "react-i18next";
import { Edit3, Save, X } from "lucide-react";
import { API } from "@/api";
import { useAppStore } from "@/stores/app-store";
import { StreamMarkdown } from "@/components/copilot/StreamMarkdown";

interface PreprocessingViewProps {
  projectName: string;
  episode: number;
  contentMode: "narration" | "drama";
}

export function PreprocessingView({
  projectName,
  episode,
  contentMode,
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
  }, [projectName, episode, editContent, pushToast]);

  const statusLabel =
    contentMode === "narration" ? t("dashboard:segment_split_complete") : t("dashboard:script_normalized_complete");

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

  return (
    <div className="flex flex-col gap-3">
      {/* Status bar */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <div className="h-1.5 w-1.5 rounded-full bg-emerald-500" />
          <span id={statusLabelId} className="text-xs text-gray-500">{statusLabel}</span>
        </div>
        <div className="flex items-center gap-1">
          {editing ? (
            <>
              <button
                type="button"
                onClick={handleSave}
                disabled={saving}
                className="flex items-center gap-1 rounded px-2 py-1 text-xs text-green-400 transition-colors hover:bg-gray-800 disabled:opacity-50"
              >
                <Save className="h-3.5 w-3.5" />
                {saving ? t("common:saving") : t("common:save")}
              </button>
              <button
                type="button"
                onClick={() => {
                  setEditing(false);
                  setEditContent(content);
                }}
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

      {/* Content */}
      {editing ? (
        <textarea
          aria-labelledby={statusLabelId}
          value={editContent}
          onChange={(e) => setEditContent(e.target.value)}
          className="min-h-[400px] w-full resize-y rounded-lg border border-gray-700 bg-gray-800 p-4 font-mono text-sm leading-relaxed text-gray-200 outline-none focus-ring focus-visible:border-indigo-500"
        />
      ) : (
        <div className="prose-invert max-w-none overflow-x-auto rounded-lg border border-gray-800 bg-gray-900/50 p-4 text-sm">
          <StreamMarkdown content={content} />
        </div>
      )}
    </div>
  );
}
