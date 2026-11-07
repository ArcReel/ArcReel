import { useState } from "react";
import { Upload } from "lucide-react";
import { useTranslation } from "react-i18next";
import { cn } from "cn";

import { getProjectDisplayName } from "@/utils/project-display";
import { SOURCE_FILE_FORMATS_LABEL } from "@/utils/source-files";

/**
 * Empty-project welcome page: title, one explanatory sentence, drop zone and guidance for starting blank.
 * Dropping or clicking passes files to the overview to open the episodes upload dialog; after upload, the overview reads source text in place.
 */
export function WelcomeCanvas({
  projectTitle,
  onSelectFiles,
}: {
  projectTitle: string | undefined;
  /** Open the upload dialog, prefilled with dropped files or an empty array for clicks. */
  onSelectFiles: (files: File[]) => void;
}) {
  const { t } = useTranslation("dashboard");
  const [dragging, setDragging] = useState(false);

  return (
    <div className="flex max-w-140 flex-col gap-6 pt-8">
      <header className="flex flex-col gap-2">
        <h1 className="display-serif text-2xl font-semibold tracking-tight break-words text-foreground">
          {t("welcome_title", { title: getProjectDisplayName(projectTitle, t("untitled_project")) })}
        </h1>
        <p className="text-sm leading-relaxed text-subtle-foreground">{t("welcome_desc")}</p>
      </header>
      <button
        type="button"
        onDragOver={(event) => {
          event.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(event) => {
          event.preventDefault();
          setDragging(false);
          onSelectFiles(Array.from(event.dataTransfer.files));
        }}
        onClick={() => onSelectFiles([])}
        className={cn(
          "focus-ring flex w-full flex-col items-center gap-2 rounded-lg border border-dashed px-6 py-12 text-center transition-colors duration-fast",
          dragging ? "border-primary bg-primary/10" : "border-input hover:border-primary/50 hover:bg-primary/5",
        )}
      >
        <Upload aria-hidden className="size-6 text-muted-foreground" />
        <span className="text-sm font-medium text-foreground">{t("welcome_drop")}</span>
        <span className="num text-xs text-muted-foreground">
          {t("welcome_formats", { formats: SOURCE_FILE_FORMATS_LABEL })}
        </span>
      </button>
      <p className="text-sm text-muted-foreground">{t("welcome_blank_hint")}</p>
    </div>
  );
}
