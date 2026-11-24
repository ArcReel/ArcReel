
export const SOURCE_FILE_EXTENSIONS = [".txt", ".md", ".docx", ".epub", ".pdf"] as const;

export const SOURCE_FILE_ACCEPT = SOURCE_FILE_EXTENSIONS.join(",");

export function isSupportedSourceFile(filename: string): boolean {
  const lower = filename.toLowerCase();
  return SOURCE_FILE_EXTENSIONS.some((ext) => lower.endsWith(ext));
}

export const SOURCE_FILE_FORMATS_LABEL = SOURCE_FILE_EXTENSIONS.map((e) =>
  e.replace(/^\./, "").toUpperCase(),
).join(" · ");
