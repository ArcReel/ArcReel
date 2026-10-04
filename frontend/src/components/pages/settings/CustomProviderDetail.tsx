import { useCallback, useEffect, useState } from "react";
import { Loader2 } from "lucide-react";
import { useTranslation } from "react-i18next";
import { API } from "@/api";
import { DetailPane } from "@/components/shared/master-detail/DetailPane";
import { Button } from "@/components/ui/button";
import type { CustomProviderInfo } from "@/types";
import { errMsg, voidCall } from "@/utils/async";
import { CustomProviderForm } from "./CustomProviderForm";

interface CustomProviderDetailProps {
  providerId: number;
  /** 深链里的模型 ID：打开时展开并定位到这一行。 */
  initialModelId?: string;
  onDeleted: () => void;
  onSaved: () => void;
}

/**
 * 自定义供应商的详情栏：取回供应商后交给表单。换供应商时由上层按 `providerId` 重建整栏。
 */
export function CustomProviderDetail({ providerId, initialModelId, onDeleted, onSaved }: CustomProviderDetailProps) {
  const { t } = useTranslation(["dashboard", "common"]);
  const [provider, setProvider] = useState<CustomProviderInfo | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    voidCall(
      API.getCustomProvider(providerId, { signal: controller.signal })
        .then((data) => {
          if (!controller.signal.aborted) setProvider(data);
        })
        .catch((err: unknown) => {
          if (!controller.signal.aborted) setLoadError(errMsg(err));
        }),
    );
    return () => {
      controller.abort();
    };
  }, [providerId, reloadKey]);

  const handleSaved = useCallback(
    (updated?: CustomProviderInfo) => {
      if (updated) setProvider(updated);
      onSaved();
    },
    [onSaved],
  );

  if (loadError) {
    return (
      <DetailPane>
        <div role="alert" className="flex flex-col items-start gap-3 p-6">
          <p className="text-sm font-medium text-warn">{t("common:load_failed")}</p>
          <p className="text-sm text-subtle-foreground">{loadError}</p>
          <Button
            variant="outline"
            size="sm"
            onClick={() => {
              setLoadError(null);
              setReloadKey((k) => k + 1);
            }}
          >
            {t("common:retry")}
          </Button>
        </div>
      </DetailPane>
    );
  }

  if (!provider) {
    return (
      <DetailPane>
        <p className="flex items-center gap-2 p-6 text-sm text-muted-foreground">
          <Loader2 aria-hidden className="size-4 animate-spin text-primary" />
          {t("common:loading")}
        </p>
      </DetailPane>
    );
  }

  return (
    <CustomProviderForm
      existing={provider}
      focusModelId={initialModelId}
      onSaved={handleSaved}
      onDeleted={onDeleted}
    />
  );
}
