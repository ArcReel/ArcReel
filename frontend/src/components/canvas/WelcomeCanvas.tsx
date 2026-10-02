import { useState, useCallback } from "react";
import { errMsg, voidPromise } from "@/utils/async";
import { useTranslation } from "react-i18next";
import {
  Upload,
  FileText,
  Sparkles,
  CheckCircle2,
  Plus,
} from "lucide-react";
import { OutputTruncationHint } from "@/components/shared/OutputTruncationHint";
import { outputTruncationOfError, type OutputTruncation } from "@/utils/output-truncation";
import { getProjectDisplayName } from "@/utils/project-display";
import { SOURCE_FILE_FORMATS_LABEL } from "@/utils/source-files";
import { SourceUploadDialog, type SourceUploadResult } from "./episodes/SourceUploadDialog";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type UploadPhase = "idle" | "has_sources" | "analyzing" | "done";

interface WelcomeCanvasProps {
  projectName: string;
  projectTitle?: string;
  /** 整本源文清单里的文件（项目相对路径），按清单顺序。 */
  wholeSourceFiles: string[];
  /** 上传对话框里的文件；null 表示对话框关着。由概览页持有，对话框打开期间欢迎页不会被换下。 */
  uploadFiles: File[] | null;
  onUploadFilesChange: (files: File[] | null) => void;
  onAnalyze?: () => Promise<void>;
}

const CARD_BG =
  "linear-gradient(180deg, oklch(0.22 0.012 265 / 0.55), oklch(0.19 0.010 265 / 0.40))";
const CARD_SHADOW =
  "inset 0 1px 0 oklch(1 0 0 / 0.04), 0 8px 24px -10px oklch(0 0 0 / 0.5)";

// ---------------------------------------------------------------------------
// WelcomeCanvas — shown when a project has no overview and no episodes yet.
// Phases: idle (no whole source) → has_sources (file list + analyze CTA) →
// analyzing → done. Uploads go through the「分集」upload dialog so every file is
// registered; the first whole-source upload starts the analysis automatically.
// ---------------------------------------------------------------------------

export function WelcomeCanvas({
  projectName,
  projectTitle,
  wholeSourceFiles,
  uploadFiles,
  onUploadFilesChange,
  onAnalyze,
}: WelcomeCanvasProps) {
  const { t } = useTranslation("dashboard");
  const [isDragging, setIsDragging] = useState(false);
  const [analysis, setAnalysis] = useState<"analyzing" | "done" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [truncation, setTruncation] = useState<OutputTruncation | null>(null);
  const displayProjectTitle = getProjectDisplayName(projectTitle, t("untitled_project"));
  const sourceFiles = wholeSourceFiles;
  const phase: UploadPhase = analysis ?? (sourceFiles.length > 0 ? "has_sources" : "idle");

  const startAnalysis = useCallback(async () => {
    if (!onAnalyze) return;
    setError(null);
    setTruncation(null);
    setAnalysis("analyzing");
    try {
      await onAnalyze();
      setAnalysis("done");
    } catch (err) {
      setError(t("analysis_failed", { message: errMsg(err) }));
      setTruncation(outputTruncationOfError(err));
      setAnalysis(null);
    }
  }, [onAnalyze, t]);

  const handleUploaded = useCallback(
    (result: SourceUploadResult) => {
      // 第一次放进整本源文时自动开始分析；已经有源文时只追加文件
      if (sourceFiles.length === 0 && result.wholeSourceFiles.length > 0) void startAnalysis();
    },
    [sourceFiles.length, startAnalysis],
  );

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(false);
    onUploadFilesChange(Array.from(e.dataTransfer.files));
  }, [onUploadFilesChange]);

  return (
    <div className="mx-auto w-full max-w-2xl space-y-6">
      {/* Welcome heading — display-serif + accent flourish */}
      <header className="text-center">
        <span
          aria-hidden
          className="mx-auto mb-4 grid h-12 w-12 place-items-center rounded-2xl"
          style={{
            background:
              "linear-gradient(135deg, oklch(0.85 0.08 295), oklch(0.70 0.12 280))",
            color: "oklch(0.14 0 0)",
            boxShadow:
              "0 10px 32px -10px var(--color-accent-glow), inset 0 1px 0 oklch(1 0 0 / 0.4)",
          }}
        >
          <Sparkles className="h-5 w-5" strokeWidth={2.2} />
        </span>
        <h1
          className="display-serif text-[28px] font-semibold leading-tight tracking-tight"
          style={{ color: "var(--color-text)" }}
        >
          {t("welcome_to_project", { title: displayProjectTitle })}
        </h1>
        <p
          className="mt-2 text-[13px] leading-relaxed"
          style={{ color: "var(--color-text-3)" }}
        >
          {phase === "idle" && t("welcome_idle_desc")}
          {phase === "has_sources" && t("welcome_has_sources_desc")}
          {phase === "analyzing" && t("analyzing_content_desc")}
          {phase === "done" && t("analysis_complete_loading")}
        </p>
      </header>

      {/* IDLE: drag-drop zone */}
      {phase === "idle" && (
        <>
          <button
            type="button"
            onDragOver={(e) => {
              e.preventDefault();
              setIsDragging(true);
            }}
            onDragLeave={() => setIsDragging(false)}
            onDrop={handleDrop}
            onClick={() => onUploadFilesChange([])}
            className="focus-ring relative w-full overflow-hidden rounded-2xl px-8 py-14 text-center transition-all"
            style={{
              border: isDragging
                ? "1px dashed var(--color-accent-soft)"
                : "1px dashed var(--color-hairline)",
              background: isDragging
                ? "linear-gradient(180deg, oklch(0.76 0.09 295 / 0.12), oklch(0.76 0.09 295 / 0.04))"
                : CARD_BG,
              boxShadow: isDragging
                ? "0 0 0 4px var(--color-accent-dim), inset 0 1px 0 oklch(1 0 0 / 0.04)"
                : CARD_SHADOW,
            }}
          >
            <span
              aria-hidden
              className="pointer-events-none absolute inset-x-0 top-0 h-px"
              style={{
                background:
                  "linear-gradient(90deg, transparent, var(--color-accent-soft), transparent)",
                opacity: isDragging ? 0.9 : 0.4,
              }}
            />
            <span
              aria-hidden
              className="mx-auto mb-3 grid h-11 w-11 place-items-center rounded-xl transition-colors"
              style={{
                background: isDragging
                  ? "var(--color-accent-dim)"
                  : "oklch(0.20 0.011 265 / 0.6)",
                border: isDragging
                  ? "1px solid var(--color-accent-soft)"
                  : "1px solid var(--color-hairline-soft)",
                color: isDragging
                  ? "var(--color-accent-2)"
                  : "var(--color-text-3)",
              }}
            >
              <Upload className="h-5 w-5" strokeWidth={2} />
            </span>
            <p
              className="display-serif text-[16px] font-semibold tracking-tight"
              style={{ color: "var(--color-text)" }}
            >
              {t("drop_files_here")}
            </p>
            <p
              className="mt-1 text-[11.5px]"
              style={{ color: "var(--color-text-4)" }}
            >
              {t("click_to_select_files")}
            </p>
            <p
              className="num mt-1.5 text-[10.5px] uppercase tracking-[0.18em]"
              style={{ color: "var(--color-text-4)" }}
            >
              {SOURCE_FILE_FORMATS_LABEL}
            </p>
          </button>

          {/* What happens next — two info rows */}
          <div className="text-left">
            <div className="mb-2.5 flex items-center gap-2">
              <span
                aria-hidden
                className="h-3 w-[3px] rounded-full"
                style={{
                  background:
                    "linear-gradient(180deg, var(--color-accent-2), var(--color-accent))",
                }}
              />
              <span
                className="text-[10.5px] font-bold uppercase"
                style={{
                  color: "var(--color-text-4)",
                  letterSpacing: "1.0px",
                }}
              >
                {t("what_happens_next")}
              </span>
            </div>
            <div className="space-y-2">
              {[
                { id: "analyze", icon: FileText, textKey: "ai_will_analyze_desc" as const },
                { id: "overview", icon: Sparkles, textKey: "overview_gen_desc" as const },
              ].map(({ id, icon: Icon, textKey }) => (
                <div
                  key={id}
                  className="flex items-start gap-3 rounded-xl px-3.5 py-2.5"
                  style={{
                    border: "1px solid var(--color-hairline-soft)",
                    background:
                      "linear-gradient(180deg, oklch(0.21 0.011 265 / 0.5), oklch(0.18 0.010 265 / 0.35))",
                    boxShadow: "inset 0 1px 0 oklch(1 0 0 / 0.03)",
                  }}
                >
                  <span
                    aria-hidden
                    className="mt-0.5 grid h-5 w-5 place-items-center rounded-md"
                    style={{
                      background: "var(--color-accent-dim)",
                      border: "1px solid var(--color-accent-soft)",
                      color: "var(--color-accent-2)",
                    }}
                  >
                    <Icon className="h-2.5 w-2.5" />
                  </span>
                  <span
                    className="text-[12px] leading-relaxed"
                    style={{ color: "var(--color-text-2)" }}
                  >
                    {t(textKey)}
                  </span>
                </div>
              ))}
            </div>
          </div>
        </>
      )}

      {/* HAS_SOURCES: file list + analyze CTA */}
      {phase === "has_sources" && (
        <div className="space-y-4">
          <section
            className="relative overflow-hidden rounded-2xl p-5 text-left"
            style={{
              border: "1px solid var(--color-hairline-soft)",
              background: CARD_BG,
              boxShadow: CARD_SHADOW,
            }}
          >
            <span
              aria-hidden
              className="pointer-events-none absolute inset-x-0 top-0 h-px"
              style={{
                background:
                  "linear-gradient(90deg, transparent, var(--color-accent-soft), transparent)",
              }}
            />
            <div className="mb-3 flex items-center gap-2.5">
              <FileText
                className="h-3.5 w-3.5"
                style={{ color: "var(--color-accent-2)" }}
              />
              <span
                className="text-[10.5px] font-bold uppercase"
                style={{
                  color: "var(--color-text-4)",
                  letterSpacing: "1.0px",
                }}
              >
                {t("uploaded_source_files")}
              </span>
              <div className="flex-1" />
              <span
                className="num text-[11px]"
                style={{ color: "var(--color-text-4)" }}
              >
                {sourceFiles.length}
              </span>
            </div>
            <div className="space-y-1.5">
              {sourceFiles.map((f) => (
                <div
                  key={f}
                  className="flex items-center gap-2 rounded-lg px-2.5 py-1.5 text-[12.5px]"
                  style={{
                    background: "oklch(0.18 0.010 265 / 0.45)",
                    border: "1px solid var(--color-hairline-soft)",
                    color: "var(--color-text-2)",
                  }}
                >
                  <FileText
                    className="h-3.5 w-3.5 shrink-0"
                    style={{ color: "var(--color-text-4)" }}
                  />
                  <span className="truncate">
                    {f.replace(/^source\//, "")}
                  </span>
                </div>
              ))}
            </div>
            <button
              type="button"
              onClick={() => onUploadFilesChange([])}
              className="focus-ring mt-3 inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-[11.5px] transition-colors hover:bg-[oklch(1_0_0_/_0.05)]"
              style={{ color: "var(--color-text-3)" }}
            >
              <Plus className="h-3 w-3" />
              {t("add_more_files")}
            </button>
          </section>

          {/* Compact drop zone */}
          <button
            type="button"
            onDragOver={(e) => {
              e.preventDefault();
              setIsDragging(true);
            }}
            onDragLeave={() => setIsDragging(false)}
            onDrop={handleDrop}
            className="focus-ring w-full rounded-xl px-4 py-3 text-[11.5px] transition-all"
            style={{
              border: isDragging
                ? "1px dashed var(--color-accent-soft)"
                : "1px dashed var(--color-hairline-soft)",
              background: isDragging
                ? "var(--color-accent-dim)"
                : "transparent",
              color: isDragging
                ? "var(--color-accent-2)"
                : "var(--color-text-4)",
            }}
          >
            {t("drop_more_files_here")}
          </button>

          {/* Primary CTA — accent gradient */}
          <button
            type="button"
            onClick={voidPromise(startAnalysis)}
            className="focus-ring relative w-full overflow-hidden rounded-xl px-6 py-3 text-[13px] font-semibold transition-transform hover:translate-y-[-1px] active:translate-y-0"
            style={{
              background:
                "linear-gradient(180deg, oklch(0.85 0.08 295), oklch(0.70 0.12 280))",
              color: "oklch(0.14 0 0)",
              boxShadow:
                "0 12px 32px -10px var(--color-accent-glow), inset 0 1px 0 oklch(1 0 0 / 0.4)",
            }}
          >
            <span className="relative inline-flex items-center gap-2">
              <Sparkles className="h-4 w-4" strokeWidth={2.4} />
              {t("start_ai_analysis")}
            </span>
          </button>
        </div>
      )}

      {/* ANALYZING */}
      {phase === "analyzing" && (
        <div
          role="status"
          aria-live="polite"
          className="relative overflow-hidden rounded-2xl p-12 text-center"
          style={{
            border: "1px solid var(--color-accent-soft)",
            background:
              "linear-gradient(180deg, oklch(0.76 0.09 295 / 0.10), oklch(0.76 0.09 295 / 0.04))",
            boxShadow:
              "0 0 0 1px var(--color-accent-dim), inset 0 1px 0 oklch(1 0 0 / 0.05)",
          }}
        >
          <span
            aria-hidden
            className="pointer-events-none absolute inset-x-0 top-0 h-px"
            style={{
              background:
                "linear-gradient(90deg, transparent, var(--color-accent-2), transparent)",
            }}
          />
          <Sparkles
            className="mx-auto h-9 w-9 animate-pulse"
            style={{ color: "var(--color-accent-2)" }}
            strokeWidth={2}
          />
          <p
            className="display-serif mt-3 text-[15px] font-semibold tracking-tight"
            style={{ color: "var(--color-text)" }}
          >
            {t("ai_analyzing")}
          </p>
          <p
            className="mt-1 text-[12px]"
            style={{ color: "var(--color-text-3)" }}
          >
            {t("extracting_metadata_desc")}
          </p>
          <div
            className="relative mx-auto mt-5 h-1 w-56 overflow-hidden rounded-full"
            style={{ background: "oklch(0.16 0.010 265 / 0.7)" }}
          >
            <div
              className="absolute inset-y-0 w-1/3 rounded-full animate-progress-pulse"
              style={{
                background:
                  "linear-gradient(90deg, transparent, var(--color-accent-2), transparent)",
                boxShadow: "0 0 8px var(--color-accent-glow)",
              }}
            />
          </div>
        </div>
      )}

      {/* DONE */}
      {phase === "done" && (
        <div
          role="status"
          aria-live="polite"
          className="rounded-2xl p-12 text-center"
          style={{
            border: "1px solid oklch(0.78 0.10 155 / 0.35)",
            background:
              "linear-gradient(180deg, oklch(0.78 0.10 155 / 0.10), oklch(0.78 0.10 155 / 0.04))",
            boxShadow: "inset 0 1px 0 oklch(1 0 0 / 0.04)",
          }}
        >
          <CheckCircle2
            className="mx-auto h-8 w-8"
            style={{ color: "var(--color-good)" }}
            strokeWidth={2}
          />
          <p
            className="display-serif mt-3 text-[15px] font-semibold tracking-tight"
            style={{ color: "var(--color-good)" }}
          >
            {t("analysis_complete")}
          </p>
        </div>
      )}

      {/* Error */}
      {error && (
        <div
          className="space-y-1.5 rounded-xl px-4 py-2.5 text-center text-[12px]"
          style={{
            border: "1px solid oklch(0.45 0.18 25 / 0.4)",
            background: "oklch(0.30 0.10 25 / 0.18)",
            color: "oklch(0.85 0.10 25)",
          }}
          role="alert"
        >
          <p className="m-0">{error}</p>
          {truncation ? <OutputTruncationHint truncation={truncation} /> : null}
        </div>
      )}

      {uploadFiles !== null ? (
        <SourceUploadDialog
          projectName={projectName}
          initialFiles={uploadFiles}
          onClose={() => onUploadFilesChange(null)}
          onUploaded={handleUploaded}
        />
      ) : null}
    </div>
  );
}
