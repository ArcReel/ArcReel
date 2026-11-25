import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { API } from "@/api";
import { useItemLocationsStore } from "./item-locations-store";

describe("item-locations-store", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    useItemLocationsStore.getState().reset(null);
  });

  afterEach(() => {
    useItemLocationsStore.getState().reset(null);
  });

  it("batches multiple visible IDs in a single microtask and splits chunks at 200 items", async () => {
    const locateSpy = vi.spyOn(API, "locateItems").mockImplementation((_project, ids) =>
      Promise.resolve({
        items: ids.map((id, idx) => ({
          episode: 1,
          item_id: id,
          position: idx + 1,
          status: "found" as const,
        })),
      }),
    );

    const ids = Array.from({ length: 205 }, (_, i) => `E1S${String(i + 1).padStart(3, "0")}`);
    const r1 = useItemLocationsStore.getState().registerVisible("demo", ids.slice(0, 100));
    const r2 = useItemLocationsStore.getState().registerVisible("demo", ids.slice(100));

    await vi.waitFor(() => expect(locateSpy).toHaveBeenCalledTimes(2));
    expect(locateSpy.mock.calls[0][1]).toHaveLength(200);
    expect(locateSpy.mock.calls[1][1]).toHaveLength(5);
    expect(useItemLocationsStore.getState().getLocation("demo", "E1S205")?.position).toBe(5);
    r1();
    r2();
  });

  it("re-queries visible unloaded IDs when SSE snapshot arrives, and ignores non-visible ones", async () => {
    const locateSpy = vi
      .spyOn(API, "locateItems")
      .mockResolvedValueOnce({
        items: [
          { episode: 1, item_id: "E1S01", position: null, status: "unloaded" },
          { episode: 1, item_id: "E1S02", position: null, status: "unloaded" },
        ],
      })
      .mockResolvedValueOnce({
        items: [{ episode: 1, item_id: "E1S01", position: 3, status: "found" }],
      });

    const release1 = useItemLocationsStore.getState().registerVisible("demo", ["E1S01"]);
    const release2 = useItemLocationsStore.getState().registerVisible("demo", ["E1S02"]);
    await vi.waitFor(() => expect(locateSpy).toHaveBeenCalledTimes(1));

    release2();
    useItemLocationsStore.getState().notifySnapshotReady("demo", false);

    await vi.waitFor(() => expect(locateSpy).toHaveBeenCalledTimes(2));
    expect(locateSpy.mock.calls[1][1]).toEqual(["E1S01"]);
    expect(useItemLocationsStore.getState().getLocation("demo", "E1S01")).toEqual({
      episode: 1,
      item_id: "E1S01",
      position: 3,
      status: "found",
    });
    release1();
  });

  it("invalidates cached entries for modified episodes and refetches visible ones", async () => {
    const locateSpy = vi
      .spyOn(API, "locateItems")
      .mockResolvedValueOnce({
        items: [{ episode: 2, item_id: "E2S01", position: 1, status: "found" }],
      })
      .mockResolvedValueOnce({
        items: [{ episode: 2, item_id: "E2S01", position: 4, status: "found" }],
      });

    const release = useItemLocationsStore.getState().registerVisible("demo", ["E2S01"]);
    await vi.waitFor(() => expect(useItemLocationsStore.getState().getLocation("demo", "E2S01")?.position).toBe(1));

    useItemLocationsStore.getState().invalidateEpisodes("demo", [2]);
    await vi.waitFor(() => expect(useItemLocationsStore.getState().getLocation("demo", "E2S01")?.position).toBe(4));
    expect(locateSpy).toHaveBeenCalledTimes(2);
    release();
  });

  it("supports locateNow for one-off async callers and records found/deleted/ambiguous states", async () => {
    vi.spyOn(API, "locateItems").mockResolvedValue({
      items: [
        { episode: 1, item_id: "E1S01", position: 2, status: "found" },
        { episode: 1, item_id: "E1S99", position: null, status: "deleted" },
        { episode: null, item_id: "S01", position: null, status: "ambiguous" },
      ],
    });

    const result = await useItemLocationsStore.getState().locateNow("demo", ["E1S01", "E1S99", "S01"]);
    expect(result.get("E1S01")?.status).toBe("found");
    expect(result.get("E1S99")?.status).toBe("deleted");
    expect(result.get("S01")?.status).toBe("ambiguous");
  });
});
