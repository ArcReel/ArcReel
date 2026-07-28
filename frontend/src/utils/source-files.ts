/**
 * Source file helpers — single source of truth for accepted novel source formats.
 *
 * Mirrors the backend whitelist (lib/source_loader/loader.py: SUPPORTED_EXTS and
 * server/routers/files.py: ALLOWED_EXTENSIONS["source"]). Keep these in sync.
 */

/** Accepted source-file extensions (lowercase, leading dot). */
export const SOURCE_FILE_EXTENSIONS = [".txt", ".md", ".docx", ".epub", ".pdf"] as const;

/** Value for an `<input type="file">` accept attribute, e.g. ".txt,.md,.docx,.epub,.pdf". */
export const SOURCE_FILE_ACCEPT = SOURCE_FILE_EXTENSIONS.join(",");

/** Whether a filename has a supported source-file extension (case-insensitive). */
export function isSupportedSourceFile(filename: string): boolean {
  const lower = filename.toLowerCase();
  return SOURCE_FILE_EXTENSIONS.some((ext) => lower.endsWith(ext));
}

/** Human-readable list of supported formats, e.g. "TXT · MD · DOCX · EPUB · PDF". */
export const SOURCE_FILE_FORMATS_LABEL = SOURCE_FILE_EXTENSIONS.map((e) =>
  e.replace(/^\./, "").toUpperCase(),
).join(" · ");
