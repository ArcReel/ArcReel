import { beforeEach, describe, expect, it, vi } from "vitest";
import { API } from "@/api";
import type { CharacterCatalogSyncJob } from "@/types";
import { useCharacterCatalogSyncStore } from "./character-catalog-sync-store";

function job(
  status: CharacterCatalogSyncJob["status"],
  updatedAt: string,
): CharacterCatalogSyncJob {
  const terminal = status === "succeeded" || status === "failed";
  return {
    job_id: "sync-1",
    job_type: "character_catalog_sync",
    status,
    phase: status === "succeeded" ? "completed" : status === "failed" ? "failed" : "syncing_characters",
    progress_current: terminal ? 2 : 1,
    progress_total: 2,
    result: status === "succeeded"
      ? {
          publishVersion: { id: "p1", name: "Published", activatedAt: "2026-08-21T00:00:00Z" },
          remoteCharacters: 2,
          added: 0,
          updated: 2,
          unchanged: 0,
          assetsDownloaded: 0,
        }
      : null,
    error_code: status === "failed" ? "character_catalog_sync_failed" : null,
    error_detail: null,
    error_message: null,
    queued_at: "2026-08-21T10:46:20.571654+00:00",
    started_at: "2026-08-21T10:46:21.114819+00:00",
    finished_at: terminal ? updatedAt : null,
    updated_at: updatedAt,
  };
}

describe("character catalog sync store", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    useCharacterCatalogSyncStore.setState(useCharacterCatalogSyncStore.getInitialState(), true);
  });

  it("accepts a terminal state even when a legacy naive timestamp looks older locally", async () => {
    useCharacterCatalogSyncStore.setState({
      job: job("running", "2026-08-21T10:46:20.571654+00:00"),
    });
    vi.spyOn(API, "getCharacterCatalogSyncStatus").mockResolvedValue({
      job: job("succeeded", "2026-08-21T10:46:24.641019"),
    });

    await useCharacterCatalogSyncStore.getState().refresh();

    expect(useCharacterCatalogSyncStore.getState().job?.status).toBe("succeeded");
  });

  it("does not regress a terminal state to an active response", async () => {
    useCharacterCatalogSyncStore.setState({
      job: job("succeeded", "2026-08-21T10:46:24.641019+00:00"),
    });
    vi.spyOn(API, "getCharacterCatalogSyncStatus").mockResolvedValue({
      job: job("running", "2026-08-21T10:46:25.000000+00:00"),
    });

    await useCharacterCatalogSyncStore.getState().refresh();

    expect(useCharacterCatalogSyncStore.getState().job?.status).toBe("succeeded");
  });
});
