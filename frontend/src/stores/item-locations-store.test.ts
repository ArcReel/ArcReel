import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createDeferred } from "@/test/deferred";
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

    await vi.waitFor(() => expect(useItemLocationsStore.getState().getLocation("demo", "E1S205")?.position).toBe(5));
    expect(locateSpy.mock.calls[0][1]).toHaveLength(200);
    expect(locateSpy.mock.calls[1][1]).toHaveLength(5);
    expect(useItemLocationsStore.getState().getLocation("demo", "E1S205")?.position).toBe(5);
    r1();
    r2();
  });

  it("retains successful chunks when a sibling lookup fails", async () => {
    const ids = Array.from({ length: 201 }, (_, i) => `E1S${i + 1}`);
    vi.spyOn(API, "locateItems")
      .mockResolvedValueOnce({ items: ids.slice(0, 200).map((item_id, i) => ({
        item_id, episode: 1, position: i + 1, status: "found",
      })) })
      .mockRejectedValueOnce(new Error("network"));
    const locations = await useItemLocationsStore.getState().locateNow("demo", ids);
    expect(locations.size).toBe(200);
    expect(locations.get("E1S200")?.position).toBe(200);
    expect(locations.has("E1S201")).toBe(false);
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
    await vi.waitFor(() => {
      for (const id of ["E1S01", "E1S02"]) {
        expect(useItemLocationsStore.getState().getLocation("demo", id)?.status).toBe("unloaded");
      }
    });

    release2();
    useItemLocationsStore.getState().notifySnapshotReady("demo", false);

    await vi.waitFor(() => expect(useItemLocationsStore.getState().getLocation("demo", "E1S01")?.position).toBe(3));
    expect(locateSpy.mock.calls[1][1]).toEqual(["E1S01"]);
    expect(useItemLocationsStore.getState().getLocation("demo", "E1S01")).toEqual({
      episode: 1,
      item_id: "E1S01",
      position: 3,
      status: "found",
    });
    release1();
  });

  it("retries an in-flight cold alias lookup after the snapshot and exposes the canonical location", async () => {
    const cold = createDeferred<Awaited<ReturnType<typeof API.locateItems>>>();
    const found = { episode: 1, item_id: "E1S01", position: 3, status: "found" as const };
    const locate = vi.spyOn(API, "locateItems").mockReturnValueOnce(cold.promise).mockResolvedValue({ items: [found] });
    const store = useItemLocationsStore.getState();
    const release = store.registerVisible("demo", ["S01"]);
    await vi.waitFor(() => expect(locate).toHaveBeenCalledTimes(1));
    store.notifySnapshotReady("demo", false);
    cold.resolve({ items: [{ episode: null, item_id: "S01", position: null, status: "unloaded" }] });
    await vi.waitFor(() => expect(store.getLocation("demo", "S01")).toEqual(found));
    release();
  });

  it("keeps an in-flight visible lookup when another episode changes", async () => {
    const pending = createDeferred<Awaited<ReturnType<typeof API.locateItems>>>();
    const locate = vi.spyOn(API, "locateItems").mockReturnValueOnce(pending.promise);
    const store = useItemLocationsStore.getState();
    const release = store.registerVisible("demo", ["E1S01"]);
    await vi.waitFor(() => expect(locate).toHaveBeenCalledTimes(1));
    store.invalidateEpisodes("demo", [2]);
    const item = { episode: 1, item_id: "E1S01", position: 5, status: "found" as const };
    pending.resolve({ items: [item] });
    await vi.waitFor(() => expect(store.getLocation("demo", "E1S01")).toEqual(item));
    expect(locate).toHaveBeenCalledTimes(1);
    release();
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

  it.each(["episode", "snapshot"])("discards a one-off lookup invalidated by %s", async (event) => {
    const old = createDeferred<Awaited<ReturnType<typeof API.locateItems>>>();
    const item = { episode: 1, item_id: "E1S01", position: 1, status: "found" as const };
    vi.spyOn(API, "locateItems").mockReturnValueOnce(old.promise).mockResolvedValue({ items: [{ ...item, position: 2 }] });
    const store = useItemLocationsStore.getState();
    const lookup = store.locateNow("demo", ["E1S01"]);
    if (event === "episode") store.invalidateEpisodes("demo", [1]);
    else store.notifySnapshotReady("demo", true);
    old.resolve({ items: [item] });
    expect((await lookup).has("E1S01")).toBe(false);
    expect((await store.locateNow("demo", ["E1S01"])).get("E1S01")?.position).toBe(2);
  });

  it("updates a known alias from a direct SSE location", async () => {
    const original = { episode: 1, item_id: "E1S01", position: 2, status: "found" as const };
    const locate = vi.spyOn(API, "locateItems").mockResolvedValue({ items: [original] });
    const store = useItemLocationsStore.getState();
    await store.locateNow("demo", ["S01"]);
    store.recordLocations("demo", [{ ...original, position: 5 }]);
    const current = await store.locateNow("demo", ["S01", "E1S01"]);
    expect(current.get("S01")?.position).toBe(5);
    expect(current.get("E1S01")?.position).toBe(5);
    expect(locate).toHaveBeenCalledTimes(1);
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
