import { useState, useEffect, useCallback, useId } from "react";
import { useTranslation } from "react-i18next";
import { ArrowLeft, FileText, Edit3, Save, X, Trash2 } from "lucide-react";
import { useLocation } from "wouter";
import { API } from "@/api";
import { voidPromise } from "@/utils/async";
import { useAppStore } from "@/stores/app-store";

// ---------------------------------------------------------------------------
// SourceFileViewer — 源文件预览/编辑组件（v3 视觉）
// ---------------------------------------------------------------------------

interface SourceFileViewerProps {
  projectName: string;
  filename: string;
}

export function SourceFileViewer({ projectName, filename }: SourceFileViewerProps) {
  const { t } = useTranslation(["dashboard", "common"]);
  const [, setLocation] = useLocation();
  const [content, setContent] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [editing, setEditing] = useState(false);
  const [editContent, setEditContent] = useState("");
  const [saving, setSaving] = useState(false);
  const filenameHeadingId = useId();

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

    return () => {
      cancelled = true;
    };
  }, [projectName, filename]);

  const handleSave = useCallback(async () => {
    setSaving(true);
    try {
      await API.saveSourceFile(projectName, filename, editContent);
      setContent(editContent);
      setEditing(false);
    } catch {
      // 静默失败
    } finally {
      setSaving(false);
    }
  }, [projectName, filename, editContent]);

  const handleDelete = useCallback(async () => {
    if (!confirm(t("dashboard:confirm_delete_source_file", { filename }))) return;
    try {
      await API.deleteSourceFile(projectName, filename);
      useAppStore.getState().invalidateSourceFiles();
      setLocation("/source");
    } catch {
      // 静默失败
    }
  }, [projectName, filename, setLocation, t]);

  if (loading) {
    return (
      <div
        className="flex h-full items-center justify-center text-[12px]"
        style={{ color: "var(--color-text-4)" }}
      >
        {t("dashboard:loading_file")}
      </div>
    );
  }

  if (content === null) {
    return (
      <div className="flex h-full flex-col">
        <ViewerToolbar
          filename={filename}
          filenameHeadingId={filenameHeadingId}
          onBack={() => setLocation("/source")}
          backLabel={t("dashboard:source_files")}
        />
        <div
          className="flex flex-1 items-center justify-center text-[12px]"
          style={{ color: "var(--color-text-4)" }}
        >
          {t("dashboard:cannot_load_file", { filename })}
        </div>
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col">
      <ViewerToolbar
        filename={filename}
        filenameHeadingId={filenameHeadingId}
        onBack={() => setLocation("/source")}
        backLabel={t("dashboard:source_files")}
      >
        {editing ? (
          <>
            <ToolbarButton
              onClick={voidPromise(handleSave)}
              disabled={saving}
              icon={<Save className="h-3.5 w-3.5" />}
              label={saving ? t("common:saving") : t("common:save")}
              tone="accent"
            />
            <ToolbarButton
              onClick={() => {
                setEditing(false);
                setEditContent(content);
              }}
              icon={<X className="h-3.5 w-3.5" />}
              label={t("common:cancel")}
            />
          </>
        ) : (
          <>
            <ToolbarButton
              onClick={() => setEditing(true)}
              icon={<Edit3 className="h-3.5 w-3.5" />}
              label={t("common:edit")}
            />
            <ToolbarButton
              onClick={voidPromise(handleDelete)}
              icon={<Trash2 className="h-3.5 w-3.5" />}
              label={t("common:delete")}
              tone="danger"
            />
          </>
        )}
      </ViewerToolbar>

      <div className="flex-1 overflow-auto p-5">
        {editing ? (
          <textarea
            aria-labelledby={filenameHeadingId}
            value={editContent}
            onChange={(e) => setEditContent(e.target.value)}
            className="focus-ring h-full w-full resize-none rounded-lg p-4 font-mono text-[13px] leading-[1.7] outline-none"
            style={{
              background:
                "linear-gradient(180deg, oklch(0.225 0.003 285 / 0.55), oklch(0.195 0.003 285 / 0.4))",
              border: "1px solid var(--color-hairline-soft)",
              color: "var(--color-text)",
              boxShadow: "inset 0 1px 0 oklch(1 0 0 / 0.03)",
            }}
          />
        ) : (
          <pre
            className="whitespace-pre-wrap rounded-lg p-4 font-mono text-[13px] leading-[1.7]"
            style={{
              background:
                "linear-gradient(180deg, oklch(0.20 0.011 265 / 0.5), oklch(0.18 0.010 265 / 0.35))",
              border: "1px solid var(--color-hairline-soft)",
              color: "var(--color-text-2)",
              boxShadow: "inset 0 1px 0 oklch(1 0 0 / 0.03)",
            }}
          >
            {content}
          </pre>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// ViewerToolbar
// ---------------------------------------------------------------------------

function ViewerToolbar({
  filename,
  filenameHeadingId,
  onBack,
  backLabel,
  children,
}: {
  filename: string;
  filenameHeadingId: string;
  onBack: () => void;
  backLabel: string;
  children?: React.ReactNode;
}) {
  return (
    <div
      className="sticky top-0 z-10 flex items-center gap-3 px-5 py-3"
      style={{
        background:
          "linear-gradient(180deg, oklch(0.20 0.012 265 / 0.85), oklch(0.18 0.010 265 / 0.65))",
        backdropFilter: "blur(10px)",
        WebkitBackdropFilter: "blur(10px)",
        borderBottom: "1px solid var(--color-hairline-soft)",
      }}
    >
      <button
        type="button"
        onClick={onBack}
        className="focus-ring inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-[11.5px] transition-colors"
        style={{
          color: "var(--color-text-3)",
          border: "1px solid var(--color-hairline)",
          background: "oklch(0.22 0.011 265 / 0.5)",
        }}
        onMouseEnter={(e) => {
          e.currentTarget.style.color = "var(--color-text)";
          e.currentTarget.style.background = "oklch(0.26 0.013 265 / 0.7)";
          e.currentTarget.style.borderColor = "var(--color-accent-soft)";
        }}
        onMouseLeave={(e) => {
          e.currentTarget.style.color = "var(--color-text-3)";
          e.currentTarget.style.background = "oklch(0.22 0.011 265 / 0.5)";
          e.currentTarget.style.borderColor = "var(--color-hairline)";
        }}
      >
        <ArrowLeft className="h-3.5 w-3.5" />
        <span>{backLabel}</span>
      </button>
      <span
        aria-hidden
        className="h-4 w-px"
        style={{ background: "var(--color-hairline-soft)" }}
      />
      <FileText
        className="h-3.5 w-3.5 shrink-0"
        style={{ color: "var(--color-text-4)" }}
      />
      <h2
        id={filenameHeadingId}
        className="display-serif min-w-0 flex-1 truncate text-[14px] font-semibold tracking-tight"
        style={{ color: "var(--color-text)" }}
        title={filename}
      >
        {filename}
      </h2>
      {children && (
        <div className="flex items-center gap-1">{children}</div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// ToolbarButton — small icon+label pill
// ---------------------------------------------------------------------------

function ToolbarButton({
  onClick,
  disabled,
  icon,
  label,
  tone,
}: {
  onClick: () => void;
  disabled?: boolean;
  icon: React.ReactNode;
  label: string;
  tone?: "accent" | "danger";
}) {
  if (tone === "accent") {
    return (
      <button
        type="button"
        onClick={onClick}
        disabled={disabled}
        className="focus-ring inline-flex items-center gap-1.5 rounded-md px-2.5 py-1 text-[11.5px] font-medium transition-transform disabled:cursor-not-allowed disabled:opacity-50"
        style={{
          color: "oklch(0.14 0 0)",
          background:
            "linear-gradient(135deg, var(--color-accent-2), var(--color-accent))",
          boxShadow:
            "inset 0 1px 0 oklch(1 0 0 / 0.35), 0 4px 14px -4px var(--color-accent-glow), 0 0 0 1px var(--color-accent-soft)",
        }}
        onMouseEnter={(e) => {
          if (!disabled) e.currentTarget.style.transform = "translateY(-1px)";
        }}
        onMouseLeave={(e) => {
          e.currentTarget.style.transform = "translateY(0)";
        }}
      >
        {icon}
        <span>{label}</span>
      </button>
    );
  }
  const dangerColor = "oklch(0.72 0.18 25)";
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className="focus-ring inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-[11.5px] transition-colors disabled:cursor-not-allowed disabled:opacity-50"
      style={{ color: "var(--color-text-3)" }}
      onMouseEnter={(e) => {
        if (!disabled) {
          if (tone === "danger") {
            e.currentTarget.style.color = dangerColor;
            e.currentTarget.style.background = "oklch(0.30 0.10 25 / 0.18)";
          } else {
            e.currentTarget.style.color = "var(--color-text)";
            e.currentTarget.style.background = "oklch(1 0 0 / 0.05)";
          }
        }
      }}
      onMouseLeave={(e) => {
        e.currentTarget.style.color = "var(--color-text-3)";
        e.currentTarget.style.background = "transparent";
      }}
    >
      {icon}
      <span>{label}</span>
    </button>
  );
}
