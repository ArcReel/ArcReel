import { useCallback, useEffect, useRef, useState } from "react";
import { useLocation } from "wouter";
import { useTranslation } from "react-i18next";
import { BookOpen, FileText, Plus, Trash2, Upload, ArrowRight } from "lucide-react";
import { API, ConflictError } from "@/api";
import { useAppStore } from "@/stores/app-store";
import { errMsg, voidPromise } from "@/utils/async";
import { ConflictModal, type ConflictResolution } from "./ConflictModal";

interface SourceFile {
  name: string;
  size: number;
  url: string;
  raw_filename?: string | null;
}

interface SourceFilesPageProps {
  projectName: string;
}

const ALLOWED_EXTENSIONS = [".txt", ".md", ".docx", ".epub", ".pdf"];

function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(2)} MB`;
}

export function SourceFilesPage({ projectName }: SourceFilesPageProps) {
  const { t } = useTranslation(["dashboard", "common"]);
  const tRef = useRef(t);
  tRef.current = t;
  const [, setLocation] = useLocation();

  const [files, setFiles] = useState<SourceFile[]>([]);
  const [loading, setLoading] = useState(true);
  const [isDragging, setIsDragging] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [conflictPrompt, setConflictPrompt] = useState<{
    existing: string;
    suggestedName: string;
    resolve: (d: ConflictResolution) => void;
  } | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const sourceFilesVersion = useAppStore((s) => s.sourceFilesVersion);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    API.listFiles(projectName)
      .then((res) => {
        if (!cancelled) setFiles(res.files?.source ?? []);
      })
      .catch(() => {
        if (!cancelled) setFiles([]);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [projectName, sourceFilesVersion]);

  const handleUpload = useCallback(
    async (file: File) => {
      const tryUpload = async (
        onConflict?: "fail" | "replace" | "rename",
      ): Promise<void> => {
        const res = await API.uploadFile(projectName, "source", file, null, {
          onConflict,
        });
        const filename = res.filename ?? file.name;
        const enc = res.used_encoding ?? null;
        const chapters = res.chapter_count ?? 0;
        const hasEncoding = enc !== null;
        let key: string;
        if (hasEncoding && chapters > 0) {
          key = "source_normalized_toast_with_chapters";
        } else if (hasEncoding) {
          key = "source_normalized_toast";
        } else if (chapters > 0) {
          key = "source_normalized_toast_native_with_chapters";
        } else {
          key = "source_normalized_toast_native";
        }
        useAppStore
          .getState()
          .pushToast(
            tRef.current(key, { filename, encoding: enc, chapters }),
            "success",
          );
      };

      setUploading(true);
      try {
        await tryUpload();
      } catch (err) {
        if (err instanceof ConflictError) {
          const decision = await new Promise<ConflictResolution>((resolve) => {
            setConflictPrompt({
              existing: err.existing,
              suggestedName: err.suggestedName,
              resolve,
            });
          });
          setConflictPrompt(null);
          if (decision === "cancel") {
            setUploading(false);
            return;
          }
          try {
            await tryUpload(decision);
          } catch (innerErr) {
            useAppStore
              .getState()
              .pushToast(
                tRef.current("dashboard:upload_failed", { message: errMsg(innerErr) }),
                "error",
              );
          }
        } else {
          useAppStore
            .getState()
            .pushToast(
              tRef.current("dashboard:upload_failed", { message: errMsg(err) }),
              "error",
            );
        }
      } finally {
        setUploading(false);
        useAppStore.getState().invalidateSourceFiles();
      }
    },
    [projectName],
  );

  const handleDelete = useCallback(
    async (filename: string) => {
      if (!confirm(tRef.current("dashboard:confirm_delete_source_file", { filename }))) return;
      try {
        await API.deleteSourceFile(projectName, filename);
        useAppStore.getState().invalidateSourceFiles();
      } catch (err) {
        useAppStore
          .getState()
          .pushToast(
            tRef.current("dashboard:delete_failed", { message: errMsg(err) }),
            "error",
          );
      }
    },
    [projectName],
  );

  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      setIsDragging(false);
      const file = e.dataTransfer.files[0];
      if (file && ALLOWED_EXTENSIONS.some((ext) => file.name.toLowerCase().endsWith(ext))) {
        void handleUpload(file);
      }
    },
    [handleUpload],
  );

  const handleFileSelect = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const file = e.target.files?.[0];
      if (file) void handleUpload(file);
      e.target.value = "";
    },
    [handleUpload],
  );

  return (
    <div className="flex h-full flex-col overflow-y-auto">
      {/* Toolbar — sticky, glass */}
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
        <span
          aria-hidden
          className="h-3 w-[3px] rounded-full"
          style={{
            background:
              "linear-gradient(180deg, var(--color-accent-2), var(--color-accent))",
            boxShadow: "0 0 8px var(--color-accent-glow)",
          }}
        />
        <h2
          className="display-serif text-[15px] font-semibold tracking-tight"
          style={{ color: "var(--color-text)" }}
        >
          {t("dashboard:source_files")}
        </h2>
        <span
          className="num inline-flex items-center justify-center rounded-md px-1.5 py-[2px] text-[10.5px]"
          style={{
            color: "var(--color-text-3)",
            background: "var(--color-accent-dim)",
            border: "1px solid var(--color-accent-soft)",
            minWidth: 22,
          }}
        >
          {String(files.length).padStart(2, "0")}
        </span>
        <div className="flex-1" />
        <input
          ref={fileInputRef}
          type="file"
          accept={ALLOWED_EXTENSIONS.join(",")}
          onChange={handleFileSelect}
          className="hidden"
        />
        <button
          type="button"
          onClick={() => fileInputRef.current?.click()}
          disabled={uploading}
          className="focus-ring inline-flex items-center gap-1.5 rounded-md px-3 py-1 text-[11.5px] font-medium transition-transform disabled:opacity-50"
          style={{
            color: "oklch(0.14 0 0)",
            background:
              "linear-gradient(135deg, var(--color-accent-2), var(--color-accent))",
            boxShadow:
              "inset 0 1px 0 oklch(1 0 0 / 0.35), 0 6px 18px -4px var(--color-accent-glow), 0 0 0 1px var(--color-accent-soft)",
          }}
          onMouseEnter={(e) => {
            if (!uploading) e.currentTarget.style.transform = "translateY(-1px)";
          }}
          onMouseLeave={(e) => {
            e.currentTarget.style.transform = "translateY(0)";
          }}
        >
          <Plus className="h-3.5 w-3.5" />
          {t("dashboard:upload_source_files")}
        </button>
      </div>

      <div className="flex-1 px-6 py-5">
        {loading ? (
          <div
            className="py-16 text-center text-[12px]"
            style={{ color: "var(--color-text-4)" }}
          >
            {t("common:loading")}
          </div>
        ) : files.length === 0 ? (
          <button
            type="button"
            onClick={() => fileInputRef.current?.click()}
            onDragOver={(e) => {
              e.preventDefault();
              setIsDragging(true);
            }}
            onDragLeave={() => setIsDragging(false)}
            onDrop={handleDrop}
            className="focus-ring group relative w-full overflow-hidden rounded-2xl px-8 py-16 text-center transition-colors"
            style={{
              border: `1px dashed ${
                isDragging ? "var(--color-accent)" : "var(--color-hairline)"
              }`,
              background: isDragging
                ? "radial-gradient(600px 280px at 50% -10%, var(--color-accent-soft), transparent 60%), oklch(0.20 0.011 265 / 0.45)"
                : "radial-gradient(600px 280px at 50% -10%, var(--color-accent-dim), transparent 60%), oklch(0.18 0.010 265 / 0.35)",
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
            <div className="mx-auto flex max-w-md flex-col items-center gap-4">
              <span
                aria-hidden
                className="grid h-14 w-14 place-items-center rounded-2xl"
                style={{
                  background:
                    "linear-gradient(135deg, var(--color-accent-dim), oklch(0.76 0.09 295 / 0.04))",
                  border: "1px solid var(--color-accent-soft)",
                  color: "var(--color-accent-2)",
                  boxShadow: "0 12px 30px -10px var(--color-accent-glow)",
                }}
              >
                <Upload className="h-6 w-6" />
              </span>
              <div className="space-y-1">
                <div
                  className="display-serif text-[18px] font-semibold tracking-tight"
                  style={{ color: "var(--color-text)" }}
                >
                  {t("dashboard:upload_source_files")}
                </div>
                <p
                  className="text-[12.5px] leading-[1.6]"
                  style={{ color: "var(--color-text-3)" }}
                >
                  {t("dashboard:source_files_drop_hint")}
                </p>
                <p
                  className="num text-[10.5px] uppercase tracking-[0.18em]"
                  style={{ color: "var(--color-text-4)" }}
                >
                  {ALLOWED_EXTENSIONS.map((e) => e.replace(/^\./, "").toUpperCase()).join(" · ")}
                </p>
              </div>
              <span
                className="mt-1 inline-flex items-center gap-1.5 rounded-md px-3 py-1 text-[11.5px] font-medium transition-transform group-hover:translate-y-[-1px]"
                style={{
                  color: "oklch(0.14 0 0)",
                  background:
                    "linear-gradient(135deg, var(--color-accent-2), var(--color-accent))",
                  boxShadow:
                    "inset 0 1px 0 oklch(1 0 0 / 0.35), 0 6px 18px -4px var(--color-accent-glow), 0 0 0 1px var(--color-accent-soft)",
                }}
              >
                <Plus className="h-3.5 w-3.5" />
                {t("dashboard:upload_source_files")}
              </span>
            </div>
          </button>
        ) : (
          <div
            onDragOver={(e) => {
              e.preventDefault();
              setIsDragging(true);
            }}
            onDragLeave={() => setIsDragging(false)}
            onDrop={handleDrop}
            className="rounded-2xl"
            style={{
              border: `1px solid ${
                isDragging ? "var(--color-accent-soft)" : "var(--color-hairline-soft)"
              }`,
              background:
                "linear-gradient(180deg, oklch(0.22 0.012 265 / 0.5), oklch(0.19 0.010 265 / 0.35))",
              boxShadow:
                "inset 0 1px 0 oklch(1 0 0 / 0.04), 0 8px 24px -10px oklch(0 0 0 / 0.5)",
              transition: "border-color .12s ease",
            }}
          >
            <div
              className="flex items-center gap-3 px-5 py-3"
              style={{
                borderBottom: "1px solid var(--color-hairline-soft)",
              }}
            >
              <BookOpen
                className="h-3.5 w-3.5"
                style={{ color: "var(--color-text-4)" }}
              />
              <span
                className="text-[10.5px] font-bold uppercase"
                style={{
                  color: "var(--color-text-4)",
                  letterSpacing: "1.0px",
                }}
              >
                {t("dashboard:uploaded_source_files")}
              </span>
              <span
                className="num text-[10px]"
                style={{ color: "var(--color-text-4)" }}
              >
                {files.length}
              </span>
              <div className="flex-1" />
              <span
                className="num text-[10px]"
                style={{ color: "var(--color-text-4)" }}
              >
                {t("dashboard:source_files_drop_inline_hint")}
              </span>
            </div>
            <ul>
              {files.map((file, idx) => (
                <li
                  key={file.name}
                  className="group flex items-center gap-3 px-5 py-3 transition-colors"
                  style={{
                    borderTop:
                      idx === 0 ? "none" : "1px solid var(--color-hairline-soft)",
                  }}
                  onMouseEnter={(e) => {
                    e.currentTarget.style.background = "oklch(0.24 0.012 265 / 0.45)";
                  }}
                  onMouseLeave={(e) => {
                    e.currentTarget.style.background = "transparent";
                  }}
                >
                  <span
                    className="num shrink-0 text-[10px]"
                    style={{ color: "var(--color-text-4)", letterSpacing: "0.5px" }}
                  >
                    {String(idx + 1).padStart(2, "0")}
                  </span>
                  <span
                    aria-hidden
                    className="grid h-9 w-9 shrink-0 place-items-center rounded-lg"
                    style={{
                      background:
                        "linear-gradient(135deg, var(--color-accent-dim), oklch(0.76 0.09 295 / 0.05))",
                      border: "1px solid var(--color-accent-soft)",
                      color: "var(--color-accent-2)",
                    }}
                  >
                    <FileText className="h-4 w-4" />
                  </span>
                  <div className="min-w-0 flex-1">
                    <div
                      className="truncate text-[13px]"
                      style={{ color: "var(--color-text)", fontWeight: 500 }}
                      title={file.name}
                    >
                      {file.name}
                    </div>
                    <div
                      className="num mt-0.5 flex items-center gap-2 text-[10.5px]"
                      style={{ color: "var(--color-text-4)" }}
                    >
                      <span>{formatFileSize(file.size)}</span>
                      {file.raw_filename && (
                        <>
                          <span style={{ color: "var(--color-hairline-strong)" }}>·</span>
                          <span className="truncate" title={file.raw_filename}>
                            {file.raw_filename}
                          </span>
                        </>
                      )}
                    </div>
                  </div>
                  <button
                    type="button"
                    onClick={() => setLocation(`/source/${encodeURIComponent(file.name)}`)}
                    className="focus-ring inline-flex items-center gap-1 rounded-md px-2 py-1 text-[11px] transition-colors"
                    style={{
                      color: "var(--color-text-3)",
                      border: "1px solid var(--color-hairline)",
                      background: "oklch(0.22 0.011 265 / 0.5)",
                    }}
                    onMouseEnter={(e) => {
                      e.currentTarget.style.color = "var(--color-text)";
                      e.currentTarget.style.borderColor = "var(--color-accent-soft)";
                    }}
                    onMouseLeave={(e) => {
                      e.currentTarget.style.color = "var(--color-text-3)";
                      e.currentTarget.style.borderColor = "var(--color-hairline)";
                    }}
                  >
                    {t("dashboard:source_open")}
                    <ArrowRight className="h-3 w-3" />
                  </button>
                  <button
                    type="button"
                    onClick={voidPromise(() => handleDelete(file.name))}
                    aria-label={t("common:delete")}
                    className="focus-ring grid h-7 w-7 place-items-center rounded-md transition-colors"
                    style={{ color: "var(--color-text-4)" }}
                    onMouseEnter={(e) => {
                      e.currentTarget.style.color = "var(--color-danger, oklch(0.72 0.18 25))";
                      e.currentTarget.style.background = "oklch(0.30 0.10 25 / 0.18)";
                    }}
                    onMouseLeave={(e) => {
                      e.currentTarget.style.color = "var(--color-text-4)";
                      e.currentTarget.style.background = "transparent";
                    }}
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </button>
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>

      {conflictPrompt && (
        <ConflictModal
          existing={conflictPrompt.existing}
          suggestedName={conflictPrompt.suggestedName}
          onResolve={conflictPrompt.resolve}
        />
      )}
    </div>
  );
}
