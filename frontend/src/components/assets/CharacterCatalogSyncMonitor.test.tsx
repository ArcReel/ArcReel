import { act, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { API } from "@/api";
import { useAppStore } from "@/stores/app-store";
import { useAssetsStore } from "@/stores/assets-store";
import { useAuthStore } from "@/stores/auth-store";
import { useCharacterCatalogSyncStore } from "@/stores/character-catalog-sync-store";
import type { CharacterCatalogSyncJob } from "@/types";
import { CharacterCatalogSyncMonitor } from "./CharacterCatalogSyncMonitor";

function runningJob(): CharacterCatalogSyncJob {
  return {
    job_id: "sync-1",
    job_type: "character_catalog_sync",
    status: "running",
    phase: "syncing_characters",
    progress_current: 1,
    progress_total: 2,
    result: null,
    error_code: null,
    error_detail: null,
    error_message: null,
    queued_at: "2026-08-21T00:00:00Z",
    started_at: "2026-08-21T00:00:01Z",
    finished_at: null,
    updated_at: "2026-08-21T00:00:02Z",
  };
}

describe("CharacterCatalogSyncMonitor", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    useAppStore.setState(useAppStore.getInitialState(), true);
    useAssetsStore.setState(useAssetsStore.getInitialState(), true);
    useCharacterCatalogSyncStore.setState(useCharacterCatalogSyncStore.getInitialState(), true);
    useAuthStore.setState({ isAuthenticated: true, isLoading: false });
  });

  it("keeps global progress visible and invalidates assets when the job finishes", async () => {
    const running = runningJob();
    vi.spyOn(API, "getCharacterCatalogSyncStatus").mockResolvedValue({ job: running });
    useCharacterCatalogSyncStore.setState({ job: running });
    render(<CharacterCatalogSyncMonitor />);

    expect(screen.getByRole("status")).toHaveTextContent("1 / 2");

    act(() => {
      useCharacterCatalogSyncStore.getState().setJob({
        ...running,
        status: "succeeded",
        phase: "completed",
        progress_current: 2,
        finished_at: "2026-08-21T00:00:03Z",
        updated_at: "2026-08-21T00:00:03Z",
        result: {
          publishVersion: { id: "p1", name: "Published", activatedAt: "2026-08-21T00:00:00Z" },
          remoteCharacters: 2,
          added: 1,
          updated: 1,
          unchanged: 0,
          assetsDownloaded: 4,
        },
      });
    });

    await waitFor(() => expect(useAssetsStore.getState().characterCatalogRevision).toBe(1));
    expect(useAppStore.getState().workspaceNotifications.at(-1)?.tone).toBe("success");
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });
});
