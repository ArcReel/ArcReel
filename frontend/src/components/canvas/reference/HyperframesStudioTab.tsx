import { useCallback, useEffect, useState } from "react";
import { ExternalLink, Loader2, RefreshCw, TriangleAlert } from "lucide-react";
import { useTranslation } from "react-i18next";
import { API } from "@/api";
import type { HyperframesWorkspaceStatus } from "@/types";
import { errMsg } from "@/utils/async";

interface HyperframesStudioTabProps {
  projectName: string;
  episode: number;
}

function studioProjectUrl(status: HyperframesWorkspaceStatus): string | null {
  if (!status.studio_url || !status.workspace_path) return null;
  const projectName = status.workspace_path.split("/").at(-1);
  if (!projectName) return status.studio_url;
  return `${status.studio_url}/#project/${encodeURIComponent(projectName)}`;
}

export function HyperframesStudioTab({ projectName, episode }: HyperframesStudioTabProps) {
  const { t } = useTranslation("dashboard");
  const requestKey = `${projectName}:${episode}`;
  const [statusState, setStatusState] = useState<{
    key: string;
    value: HyperframesWorkspaceStatus;
  } | null>(null);
  const [errorState, setErrorState] = useState<{ key: string; message: string } | null>(null);
  const [retryToken, setRetryToken] = useState(0);

  useEffect(() => {
    let cancelled = false;
    API.startHyperframesStudio(projectName, episode)
      .then((next) => {
        if (!cancelled) setStatusState({ key: `${projectName}:${episode}`, value: next });
      })
      .catch((reason) => {
        if (!cancelled) {
          setErrorState({ key: `${projectName}:${episode}`, message: errMsg(reason) });
        }
      });
    return () => {
      cancelled = true;
    };
  }, [projectName, episode, retryToken]);

  const retry = useCallback(() => {
    setErrorState(null);
    setStatusState(null);
    setRetryToken((value) => value + 1);
  }, []);
  const status = statusState?.key === requestKey ? statusState.value : null;
  const error = errorState?.key === requestKey ? errorState.message : null;
  const studioUrl = status ? studioProjectUrl(status) : null;

  if (error) {
    return (
      <div className="grid h-full place-items-center bg-[oklch(0.16_0.01_260)] px-6">
        <div className="max-w-lg rounded-xl border border-red-500/20 bg-red-500/5 p-5 text-center">
          <TriangleAlert className="mx-auto mb-3 h-6 w-6 text-red-400" aria-hidden="true" />
          <h3 className="text-sm font-semibold text-[var(--color-text)]">
            {t("hyperframes_studio_start_failed")}
          </h3>
          <p className="mt-2 break-words text-xs leading-5 text-[var(--color-text-3)]">{error}</p>
          <button
            type="button"
            onClick={retry}
            className="focus-ring mt-4 inline-flex items-center gap-1.5 rounded-md border border-[var(--color-hairline)] px-3 py-1.5 text-xs text-[var(--color-text-2)]"
          >
            <RefreshCw className="h-3.5 w-3.5" aria-hidden="true" />
            {t("hyperframes_retry")}
          </button>
        </div>
      </div>
    );
  }

  if (!studioUrl) {
    return (
      <div
        role="status"
        className="flex h-full items-center justify-center gap-2 bg-[oklch(0.16_0.01_260)] text-xs text-[var(--color-text-3)]"
      >
        <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
        {t("hyperframes_studio_starting")}
      </div>
    );
  }

  return (
    <div className="flex h-full min-h-0 flex-col bg-[oklch(0.12_0.01_260)]">
      <div className="flex h-9 shrink-0 items-center justify-end border-b border-[var(--color-hairline)] px-3">
        <a
          href={studioUrl}
          target="_blank"
          rel="noreferrer"
          className="focus-ring inline-flex items-center gap-1.5 rounded px-2 py-1 text-[11px] text-[var(--color-text-3)] hover:text-[var(--color-text)]"
        >
          <ExternalLink className="h-3 w-3" aria-hidden="true" />
          {t("hyperframes_open_new_window")}
        </a>
      </div>
      <iframe
        key={studioUrl}
        src={studioUrl}
        title={t("hyperframes_studio_title")}
        className="min-h-0 flex-1 border-0 bg-[#0b0f14]"
        sandbox="allow-downloads allow-forms allow-modals allow-popups allow-same-origin allow-scripts"
        allow="clipboard-read; clipboard-write; fullscreen"
        allowFullScreen
      />
    </div>
  );
}
