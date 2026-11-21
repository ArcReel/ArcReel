/**
 * Source file helpers — single source of truth for accepted novel source formats.
 *
 * Mirrors the backend whitelist (lib/script/source_loader/loader.py: SUPPORTED_EXTS and
 * server/routers/files.py: ALLOWED_EXTENSIONS["source"]). Keep these in sync.
 */

export const SOURCE_FILE_EXTENSIONS = [".txt", ".md", ".docx", ".epub", ".pdf"] as const;

export const SOURCE_FILE_ACCEPT = SOURCE_FILE_EXTENSIONS.join(",");

/** Whether a filename has a supported source-file extension (case-insensitive). */
export function isSupportedSourceFile(filename: string): boolean {
  const lower = filename.toLowerCase();
  return SOURCE_FILE_EXTENSIONS.some((ext) => lower.endsWith(ext));
}

export const SOURCE_FILE_FORMATS_LABEL = SOURCE_FILE_EXTENSIONS.map((e) =>
  e.replace(/^\./, "").toUpperCase(),
).join(" · ");
