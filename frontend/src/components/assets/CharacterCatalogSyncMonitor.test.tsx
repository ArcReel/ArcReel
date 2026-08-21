import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
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

  it("collapses to a compact progress control and expands without stopping the job", async () => {
    const running = runningJob();
    vi.spyOn(API, "getCharacterCatalogSyncStatus").mockResolvedValue({ job: running });
    useCharacterCatalogSyncStore.setState({ job: running });
    render(<CharacterCatalogSyncMonitor />);

    fireEvent.click(screen.getByRole("button", { name: "缩小同步进度" }));

    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "展开同步进度" })).toHaveTextContent("50%");

    act(() => {
      useCharacterCatalogSyncStore.getState().setJob({
        ...running,
        progress_current: 2,
        progress_total: 3,
        updated_at: "2026-08-21T00:00:03Z",
      });
    });
    expect(screen.getByRole("button", { name: "展开同步进度" })).toHaveTextContent("67%");

    fireEvent.click(screen.getByRole("button", { name: "展开同步进度" }));
    expect(screen.getByRole("status")).toHaveTextContent("2 / 3");
  });

  it("starts a different job expanded after the previous job was collapsed", () => {
    const running = runningJob();
    vi.spyOn(API, "getCharacterCatalogSyncStatus").mockResolvedValue({ job: running });
    useCharacterCatalogSyncStore.setState({ job: running });
    render(<CharacterCatalogSyncMonitor />);
    fireEvent.click(screen.getByRole("button", { name: "缩小同步进度" }));

    act(() => {
      useCharacterCatalogSyncStore.getState().setJob({
        ...running,
        job_id: "sync-2",
        progress_current: 0,
        updated_at: "2026-08-21T00:01:00Z",
      });
    });

    expect(screen.getByRole("status")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "缩小同步进度" })).toBeInTheDocument();
  });
});
