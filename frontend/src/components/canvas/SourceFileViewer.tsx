import { useState, useEffect, useCallback, useId } from "react";
import { useTranslation } from "react-i18next";
import { FileText, Edit3, Save, X, Trash2 } from "lucide-react";
import { useLocation } from "wouter";
import { API } from "@/api";
import { voidPromise } from "@/utils/async";
import { useAppStore } from "@/stores/app-store";

// ---------------------------------------------------------------------------
// SourceFileViewer — 源文件预览/编辑组件
// ---------------------------------------------------------------------------

interface SourceFileViewerProps {
  projectName: string;
  filename: string;
}

export function SourceFileViewer({ projectName, filename }: SourceFileViewerProps) {
  const { t } = useTranslation("dashboard");
  const [, setLocation] = useLocation();
  const [content, setContent] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [editing, setEditing] = useState(false);
  const [editContent, setEditContent] = useState("");
  const [saving, setSaving] = useState(false);
  const filenameHeadingId = useId();

  // 加载文件内容
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setEditing(false);

    API.getSourceContent(projectName, filename)
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

    return () => { cancelled = true; };
  }, [projectName, filename]);

  // 保存文件
  const handleSave = useCallback(async () => {
    setSaving(true);
    try {
      await API.saveSourceFile(projectName, filename, editContent);
      setContent(editContent);
      setEditing(false);
    } catch {
      // 可以添加 toast 提示
    } finally {
      setSaving(false);
    }
  }, [projectName, filename, editContent]);

  // 删除文件
  const handleDelete = useCallback(async () => {
    if (!confirm(t("confirm_delete_source_file", { filename }))) return;
    try {
      await API.deleteSourceFile(projectName, filename);
      useAppStore.getState().invalidateSourceFiles();
      setLocation("/");
    } catch {
      // 可以添加 toast 提示
    }
  }, [projectName, filename, setLocation, t]);

  if (loading) {
    return (
      <div className="flex h-full items-center justify-center text-gray-500">
        {t("loading_file")}
      </div>
    );
  }

  if (content === null) {
    return (
      <div className="flex h-full items-center justify-center text-gray-500">
        {t("cannot_load_file", { filename })}
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col">
      {/* Toolbar */}
      <div className="flex items-center justify-between border-b border-gray-800 px-4 py-2">
        <div className="flex items-center gap-2">
          <FileText className="h-4 w-4 text-gray-400" />
          <h2 id={filenameHeadingId} className="text-sm font-medium text-gray-200">{filename}</h2>
        </div>
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
                onClick={() => { setEditing(false); setEditContent(content); }}
                className="flex items-center gap-1 rounded px-2 py-1 text-xs text-gray-400 transition-colors hover:bg-gray-800"
              >
                <X className="h-3.5 w-3.5" />
                {t("common:cancel")}
              </button>
            </>
          ) : (
            <>
              <button
                type="button"
                onClick={() => setEditing(true)}
                className="flex items-center gap-1 rounded px-2 py-1 text-xs text-gray-400 transition-colors hover:bg-gray-800 hover:text-gray-200"
              >
                <Edit3 className="h-3.5 w-3.5" />
                {t("common:edit")}
              </button>
              <button
                type="button"
                onClick={voidPromise(handleDelete)}
                className="flex items-center gap-1 rounded px-2 py-1 text-xs text-gray-400 transition-colors hover:bg-gray-800 hover:text-red-400"
              >
                <Trash2 className="h-3.5 w-3.5" />
                {t("common:delete")}
              </button>
            </>
          )}
        </div>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-auto p-4">
        {editing ? (
          <textarea
            aria-labelledby={filenameHeadingId}
            value={editContent}
            onChange={(e) => setEditContent(e.target.value)}
            className="h-full w-full resize-none rounded-lg border border-gray-700 bg-gray-800 p-4 font-mono text-sm leading-relaxed text-gray-200 outline-none focus:border-indigo-500"
          />
        ) : (
          <pre className="whitespace-pre-wrap font-mono text-sm leading-relaxed text-gray-300">
            {content}
          </pre>
        )}
      </div>
    </div>
  );
}
