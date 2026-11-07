import { useCallback, useEffect, useState } from "react";
import { API } from "@/api";
import { useActiveResourceIds } from "@/stores/tasks-store";
import type { CharacterDerivativeStatus } from "@/types";

/**
 * Fetch all derivative asset images and staleness flags for one character.
 *
 * Staleness compares artifact manifests with canonical state by reading files and computing fingerprints; it is not sent with project data. Skip requests without derivatives. Refetch when `revision`
 * changes after registration/base-image changes, derivative-task occupancy changes after task completion, or `refresh` is called.
 * AbortSignal invalidates the previous in-flight request; retain previous values on failure, preferring briefly old flags to flashing no image.
 */
export function useCharacterDerivativeSheets(
  projectName: string,
  characterName: string,
  revision: string,
  enabled: boolean,
): { statuses: Record<string, CharacterDerivativeStatus>; refresh: () => void } {
  const [statuses, setStatuses] = useState<Record<string, CharacterDerivativeStatus>>({});
  const [reloadToken, setReloadToken] = useState(0);
  const activeCount = useActiveResourceIds("character_derivative", projectName).size;
  const refresh = useCallback(() => setReloadToken((n) => n + 1), []);

  useEffect(() => {
    if (!enabled) return;
    const controller = new AbortController();
    void API.getCharacterDerivativeSheets(projectName, characterName, { signal: controller.signal })
      .then((res) => {
        if (!controller.signal.aborted) setStatuses(res.derivatives ?? {});
      })
      .catch(() => {
        /* Read failures, including aborted requests, retain previous state without affecting derivative registration or writes */
      });
    return () => controller.abort();
  }, [enabled, projectName, characterName, revision, activeCount, reloadToken]);

  return { statuses, refresh };
}
