import { useTranslation } from "react-i18next";

import { ObjectStorageBlock } from "./ObjectStorageBlock";

export function MediaStorageSection() {
  const { t } = useTranslation("dashboard");
  return (
    <div className="flex flex-col gap-6">
      <div className="flex min-w-0 flex-col gap-1">
        <h2 className="text-lg font-medium">{t("settings_media_storage")}</h2>
        <p className="max-w-[40em] text-sm text-muted-foreground">{t("media_storage_section_desc")}</p>
      </div>

      <ObjectStorageBlock />
    </div>
  );
}
