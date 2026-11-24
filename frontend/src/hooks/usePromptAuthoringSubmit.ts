import { useState } from "react";
import { useTranslation } from "react-i18next";
import { ApiRequestError } from "@/api";
import { enqueuePromptAuthoring, promptAuthoringResourceId } from "@/actions/generation";
import { useAppStore } from "@/stores/app-store";
import { isResourceBusy, useActiveResourceIds } from "@/stores/tasks-store";
import type { AuthorPromptsRequest, PromptOverwrite } from "@/types";
import { errMsg } from "@/utils/async";

function readPromptOverwrite(err: unknown): PromptOverwrite | null {
  if (!(err instanceof ApiRequestError) || err.status !== 409) return null;
  const diagnostic = err.diagnostic as { prompt_overwrite?: PromptOverwrite } | undefined;
  return diagnostic?.prompt_overwrite ?? null;
}

export function usePromptAuthoringSubmit(projectName: string, episode: number) {
  const { t } = useTranslation("dashboard");
  const resourceId = promptAuthoringResourceId(episode);
  const busy = useActiveResourceIds("text_episode_script", projectName).has(resourceId);
  const [submitting, setSubmitting] = useState(false);
  const [overwriteState, setOverwriteState] = useState<{
    overwrite: PromptOverwrite;
    request: AuthorPromptsRequest;
  } | null>(null);

  async function enqueue(request: AuthorPromptsRequest): Promise<boolean> {
    if (submitting) return false;
    if (isResourceBusy("text_episode_script", projectName, resourceId)) {
      useAppStore.getState().pushToast(t("prompt_authoring_busy"), "error");
      return false;
    }
    setSubmitting(true);
    try {
      await enqueuePromptAuthoring(projectName, episode, request);
      setOverwriteState(null);
      return true;
    } catch (err) {
      const loss = readPromptOverwrite(err);
      setOverwriteState(loss ? { overwrite: loss, request } : null);
      if (!loss) useAppStore.getState().pushToast(errMsg(err), "error");
      return false;
    } finally {
      setSubmitting(false);
    }
  }

  function confirmOverwrite(revision: string): Promise<boolean> {
    if (!overwriteState) return Promise.resolve(false);
    return enqueue({ ...overwriteState.request, overwrite_revision: revision });
  }

  return {
    busy,
    submitting,
    overwrite: overwriteState?.overwrite ?? null,
    enqueue,
    confirmOverwrite,
    closeOverwrite: () => setOverwriteState(null),
  };
}
