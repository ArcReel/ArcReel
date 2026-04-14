
import { useState, useRef, useCallback, useEffect } from "react";
import { voidCall, voidPromise } from "@/utils/async";
import { useTranslation } from "react-i18next";
import { Upload, FileText, Sparkles, Loader2, CheckCircle2, Plus } from "lucide-react";
import { API } from "@/api";
import { useAppStore } from "@/stores/app-store";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type UploadPhase = "loading" | "idle" | "has_sources" | "uploading" | "analyzing" | "done";

interface WelcomeCanvasProps {
  projectName: string;
  projectTitle?: string;
  onUpload?: (file: File) => Promise<void>;
  onAnalyze?: () => Promise<void>;
}

// ---------------------------------------------------------------------------
// WelcomeCanvas — shown when a project has no overview yet.
// Two entry states:
//   - idle: no source files → show drag-drop upload zone
//   - has_sources: source files exist → show file list + "开始分析" button
// Then: uploading → analyzing → done
// ---------------------------------------------------------------------------

export function WelcomeCanvas({
  projectName,
  projectTitle,
  onUpload,
  onAnalyze,
}: WelcomeCanvasProps) {
  const { t } = useTranslation("dashboard");
  const [isDragging, setIsDragging] = useState(false);
  const [phase, setPhase] = useState<UploadPhase>("loading");
  const [sourceFiles, setSourceFiles] = useState<string[]>([]);
  const [fileName, setFileName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const sourceFilesVersion = useAppStore((s) => s.sourceFilesVersion);
  const displayProjectTitle = projectTitle?.trim() || projectName;

  // Check existing source files on mount and when sourceFilesVersion changes
  useEffect(() => {
    let cancelled = false;
    voidCall((async () => {
      try {
        const res = await API.listFiles(projectName);
        // Backend returns grouped object: { files: { source: [{name, size, url}, ...], ... } }
        const sourceGroup = res.files?.source ?? [];
        const sources = sourceGroup.map((f) => `source/${f.name}`);
        if (!cancelled) {
          setSourceFiles(sources);
          // Only update phase if we're in a state that should react to file list changes
          setPhase((prev) => {
            if (prev === "loading" || prev === "idle" || prev === "has_sources") {
              return sources.length > 0 ? "has_sources" : "idle";
            }
            return prev;
          });
        }
      } catch {
        if (!cancelled) setPhase((prev) => prev === "loading" ? "idle" : prev);
      }
    })());
    return () => { cancelled = true; };
  }, [projectName, sourceFilesVersion]);

  const processFile = useCallback(
    async (file: File) => {
      if (!onUpload) return;
      setFileName(file.name);
      setError(null);

      // Phase: Upload
      setPhase("uploading");
      try {
        await onUpload(file);
      } catch (err) {
        setError(`${t("upload_failed")}${(err as Error).message}`);
        setPhase(sourceFiles.length > 0 ? "has_sources" : "idle");
        return;
      }

      // Update source files list
      setSourceFiles((prev) => {
        const name = `source/${file.name}`;
        return prev.includes(name) ? prev : [...prev, name];
      });

      // Notify sidebar to refresh
      useAppStore.getState().invalidateSourceFiles();

      // Transition to has_sources so user can review or add more
      setPhase("has_sources");
    },
    [onUpload, sourceFiles.length, t],
  );

  const startAnalysis = useCallback(async () => {
    if (!onAnalyze) return;
    setError(null);
    setPhase("analyzing");
    try {
      await onAnalyze();
      setPhase("done");
    } catch (err) {
      setError(`${t("analysis_failed")}${(err as Error).message}`);
      setPhase("has_sources");
    }
  }, [onAnalyze, t]);

  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      setIsDragging(false);
      const file = e.dataTransfer.files[0];
      if (file && (file.name.endsWith(".txt") || file.name.endsWith(".md"))) {
        voidCall(processFile(file));
      }
    },
    [processFile],
  );

  const handleFileSelect = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const file = e.target.files?.[0];
      if (file) voidCall(processFile(file));
      e.target.value = "";
    },
    [processFile],
  );

  if (phase === "loading") {
    return (
      <div className="flex h-full items-center justify-center">
        <Loader2 className="h-6 w-6 text-gray-500 animate-spin" />
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col items-center justify-center p-8">
      <div className="max-w-lg text-center space-y-6">
        {/* Welcome heading */}
        <div>
          <Sparkles className="mx-auto mb-3 h-10 w-10 text-indigo-400" />
          <h1 className="text-2xl font-bold text-gray-100">
            {t("welcome_to_project", { title: displayProjectTitle })}
          </h1>
          <p className="mt-2 text-sm text-gray-400">
            {phase === "idle" && t("welcome_idle_desc")}
            {phase === "has_sources" && t("welcome_has_sources_desc")}
            {phase === "uploading" && t("uploading_file", { name: fileName })}
            {phase === "analyzing" && t("analyzing_content_desc")}
            {phase === "done" && t("analysis_complete_loading")}
          </p>
        </div>

        {/* ---- IDLE: No source files, show upload zone ---- */}
        {phase === "idle" && (
          <button
            type="button"
            onDragOver={(e) => { e.preventDefault(); setIsDragging(true); }}
            onDragLeave={() => setIsDragging(false)}
            onDrop={handleDrop}
            onClick={() => fileInputRef.current?.click()}
            className={`w-full cursor-pointer rounded-xl border-2 border-dashed p-12 transition-colors text-center ${
              isDragging
                ? "border-indigo-500 bg-indigo-500/10"
                : "border-gray-700 hover:border-gray-600 hover:bg-gray-900/50"
            }`}
          >
            <Upload
              className={`mx-auto h-8 w-8 ${isDragging ? "text-indigo-400" : "text-gray-500"}`}
            />
            <p className="mt-3 text-sm text-gray-300">{t("drop_files_here")}</p>
            <p className="mt-1 text-xs text-gray-500">
              {t("click_to_select_files")}
            </p>
            <input
              ref={fileInputRef}
              type="file"
              accept=".txt,.md"
              aria-label={t("upload_script_file_aria")}
              className="hidden"
              onChange={handleFileSelect}
            />
          </button>
        )}

        {/* ---- HAS_SOURCES: Source files exist, show list + analyze button ---- */}
        {phase === "has_sources" && (
          <div className="space-y-4">
            {/* Source file list */}
            <div className="rounded-xl border border-gray-800 bg-gray-900 p-4 text-left">
              <p className="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-3">
                {t("uploaded_source_files")}
              </p>
              <div className="space-y-1.5">
                {sourceFiles.map((f) => (
                  <div key={f} className="flex items-center gap-2 text-sm text-gray-300">
                    <FileText className="h-4 w-4 text-gray-500 shrink-0" />
                    <span className="truncate">{f.replace(/^source\//, "")}</span>
                  </div>
                ))}
              </div>
              {/* Add more files */}
              <button
                type="button"
                onClick={() => fileInputRef.current?.click()}
                className="mt-3 flex items-center gap-1.5 text-xs text-gray-400 hover:text-gray-300 transition-colors"
              >
                <Plus className="h-3.5 w-3.5" />
                {t("add_more_files")}
              </button>
              <input
                ref={fileInputRef}
                type="file"
                accept=".txt,.md"
                aria-label={t("upload_script_file_aria")}
                className="hidden"
                onChange={handleFileSelect}
              />
            </div>

            {/* Drop zone (compact) */}
            <button
              type="button"
              onDragOver={(e) => { e.preventDefault(); setIsDragging(true); }}
              onDragLeave={() => setIsDragging(false)}
              onDrop={handleDrop}
              className={`w-full rounded-lg border border-dashed p-4 text-xs transition-colors ${
                isDragging
                  ? "border-indigo-500 bg-indigo-500/10 text-indigo-400"
                  : "border-gray-700 text-gray-500 hover:border-gray-600"
              }`}
            >
              {t("drop_more_files_here")}
            </button>

            {/* Analyze button */}
            <button
              type="button"
              onClick={voidPromise(startAnalysis)}
              className="w-full rounded-xl bg-indigo-600 px-6 py-3 text-sm font-medium text-white hover:bg-indigo-500 transition-colors"
            >
              <Sparkles className="inline-block h-4 w-4 mr-2 -mt-0.5" />
              {t("start_ai_analysis")}
            </button>
          </div>
        )}

        {/* ---- UPLOADING ---- */}
        {phase === "uploading" && (
          <div className="rounded-xl border border-gray-700 bg-gray-900/50 p-12">
            <Loader2 className="mx-auto h-8 w-8 text-indigo-400 animate-spin" />
            <p className="mt-3 text-sm text-gray-300">{t("uploading")}</p>
            <p className="mt-1 text-xs text-gray-500">{fileName}</p>
          </div>
        )}

        {/* ---- ANALYZING ---- */}
        {phase === "analyzing" && (
          <div className="rounded-xl border border-indigo-500/30 bg-indigo-500/5 p-12">
            <Sparkles className="mx-auto h-10 w-10 text-indigo-400 animate-pulse" />
            <p className="mt-3 text-sm text-indigo-300 font-medium">{t("ai_analyzing")}</p>
            <p className="mt-1 text-xs text-gray-400">{t("extracting_metadata_desc")}</p>
            <div className="mt-4 mx-auto w-48 h-1 rounded-full bg-gray-800 overflow-hidden">
              <div className="h-full rounded-full bg-indigo-600 animate-progress" />
            </div>
          </div>
        )}

        {/* ---- DONE ---- */}
        {phase === "done" && (
          <div className="rounded-xl border border-green-500/30 bg-green-500/5 p-12">
            <CheckCircle2 className="mx-auto h-8 w-8 text-green-400" />
            <p className="mt-3 text-sm text-green-300">{t("analysis_complete")}</p>
          </div>
        )}

        {/* Error message */}
        {error && (
          <p className="text-sm text-red-400">{error}</p>
        )}

        {/* Quick tips — only in idle state */}
        {phase === "idle" && (
          <div className="text-left space-y-2">
            <p className="text-xs font-semibold text-gray-500 uppercase tracking-wider">
              {t("what_happens_next")}
            </p>
            <div className="space-y-1.5 text-xs text-gray-400">
              <div className="flex items-start gap-2">
                <FileText className="mt-0.5 h-3.5 w-3.5 text-gray-500 shrink-0" />
                <span>{t("ai_will_analyze_desc")}</span>
              </div>
              <div className="flex items-start gap-2">
                <Sparkles className="mt-0.5 h-3.5 w-3.5 text-gray-500 shrink-0" />
                <span>{t("overview_gen_desc")}</span>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
