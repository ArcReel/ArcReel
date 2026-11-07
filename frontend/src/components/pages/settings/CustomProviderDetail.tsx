import { useCallback, useEffect, useState } from "react";
import { Loader2 } from "lucide-react";
import { useTranslation } from "react-i18next";
import { API } from "@/api";
import { DetailPane } from "@/components/shared/master-detail/DetailPane";
import { Button } from "@/components/ui/button";
import type { CustomProviderInfo, ProviderMultiplierInfo } from "@/types";
import { errMsg, voidCall } from "@/utils/async";
import { CustomProviderForm } from "./CustomProviderForm";

interface CustomProviderDetailProps {
  providerId: number;
  /** Model ID from a deep link: expand and locate this row when opening. */
  initialModelId?: string;
  onDeleted: () => void;
  onSaved: () => void;
}

/**
 * Custom-provider detail pane: fetch the provider and its provider multiplier before passing them to the form. The parent recreates the whole pane by `providerId` when switching providers.
 * Render only once both arrive: if the multiplier arrives after editing begins, the editor treats it as an external update and saves the old value back.
 */
export function CustomProviderDetail({ providerId, initialModelId, onDeleted, onSaved }: CustomProviderDetailProps) {
  const { t } = useTranslation(["dashboard", "common"]);
  const [provider, setProvider] = useState<CustomProviderInfo | null>(null);
  const [multiplier, setMultiplier] = useState<ProviderMultiplierInfo | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    voidCall(
      Promise.all([
        API.getCustomProvider(providerId, { signal: controller.signal }),
        API.getProviderMultiplier(`custom-${providerId}`, { signal: controller.signal }),
      ])
        .then(([data, multiplierInfo]) => {
          if (controller.signal.aborted) return;
          setProvider(data);
          setMultiplier(multiplierInfo);
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

  if (!provider || !multiplier) {
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
      providerMultiplier={multiplier}
      focusModelId={initialModelId}
      onSaved={handleSaved}
      onDeleted={onDeleted}
    />
  );
}
