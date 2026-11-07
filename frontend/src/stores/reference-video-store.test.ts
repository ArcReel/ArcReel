import { beforeEach, describe, expect, it, vi, afterEach } from "vitest";
import { act } from "@testing-library/react";
import { useReferenceVideoStore } from "./reference-video-store";
import { API } from "@/api";
import { makeReferenceUnitCapability } from "@/test/factories";
import type { ReferenceVideoUnit, UnitGeneratedAssets } from "@/types";

function mkUnit(
  id: string,
  overrides: Partial<ReferenceVideoUnit> = {},
): ReferenceVideoUnit & { generated_assets: UnitGeneratedAssets } {
  return {
    unit_id: id,
    text: "x",
    duration_seconds: 3,
    note: null,
    generated_assets: {
      storyboard_image: null,
      storyboard_last_image: null,
      grid_id: null,
      grid_cell_index: null,
      video_clip: null,
      video_uri: null,
      status: "pending",
      video_generated_at: null,
    },
    ...overrides,
  };
}


describe("reference-video-store", () => {
  beforeEach(() => {
    useReferenceVideoStore.setState({
      unitsByEpisode: {},
      unitCapabilitiesByEpisode: {},
      selectedUnitId: null,
      loading: false,
      error: null,
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("loadUnits populates unitsByEpisode and clears loading", async () => {
    vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValueOnce({
      units: [mkUnit("E1U1"), mkUnit("E1U2")],
      unit_capabilities: { E1U1: makeReferenceUnitCapability("E1U1"), E1U2: makeReferenceUnitCapability("E1U2", { hydrated_capability: "r2v" }) },
    });

    await act(async () => {
      await useReferenceVideoStore.getState().loadUnits("proj", 1);
    });

    const state = useReferenceVideoStore.getState();
    expect(state.unitsByEpisode["proj::1"]).toHaveLength(2);
    // Unit bucket/tier results persist with lists; canvases read server results only.
    expect(state.unitCapabilitiesByEpisode["proj::1"].E1U2.hydrated_capability).toBe("r2v");
    expect(state.loading).toBe(false);
    expect(state.error).toBeNull();
  });

  it("loadUnits captures error and clears loading", async () => {
    vi.spyOn(API, "listReferenceVideoUnits").mockRejectedValueOnce(new Error("boom"));

    await act(async () => {
      await useReferenceVideoStore.getState().loadUnits("proj", 1);
    });

    const state = useReferenceVideoStore.getState();
    expect(state.error).toBe("boom");
    expect(state.loading).toBe(false);
  });

  it("loadUnits 丢弃迟到响应，不让旧结果盖住新结果", async () => {
    // Canvas effects and task-completion invalidation may overlap two same-key loadUnits calls. Earlier calls returning later
    // overwrite generated-video newer data with old, leaving no-video displays until the next invalidation.
    const releases: Array<(units: ReferenceVideoUnit[]) => void> = [];
    vi.spyOn(API, "listReferenceVideoUnits").mockImplementation(
      () => new Promise((resolve) => releases.push((units) => resolve({ units, unit_capabilities: {} }))),
    );

    let first: Promise<void>;
    let second: Promise<void>;
    await act(async () => {
      first = useReferenceVideoStore.getState().loadUnits("proj", 1);
      second = useReferenceVideoStore.getState().loadUnits("proj", 1);
      // Later requests return first with videos; earlier requests arrive late without.
      releases[1]([mkUnit("E1U1", { generated_assets: { ...mkUnit("E1U1").generated_assets, video_clip: "v.mp4" } })]);
      releases[0]([mkUnit("E1U1")]);
      await Promise.all([first, second]);
    });

    const units = useReferenceVideoStore.getState().unitsByEpisode["proj::1"];
    expect(units?.[0].generated_assets?.video_clip).toBe("v.mp4");
  });

  it("迟到的加载不撤销加载期间落定的增删改排序", async () => {
    // Write entries read postwrite lists; in-flight GET read prewrite state. Late writes would undo
    // fresh user edits visually until the next invalidation restores them.
    const releases: Array<(units: ReferenceVideoUnit[]) => void> = [];
    vi.spyOn(API, "listReferenceVideoUnits").mockImplementation(
      () => new Promise((resolve) => releases.push((units) => resolve({ units, unit_capabilities: {} }))),
    );
    vi.spyOn(API, "addReferenceVideoUnit").mockResolvedValueOnce({
      unit: mkUnit("E1U9"),
      unit_capability: makeReferenceUnitCapability("E1U9"),
    });

    await act(async () => {
      const load = useReferenceVideoStore.getState().loadUnits("proj", 1);
      await useReferenceVideoStore.getState().addUnit("proj", 1, { prompt: "p" });
      releases[0]([mkUnit("E1U1")]);
      await load;
    });

    const state = useReferenceVideoStore.getState();
    expect(state.unitsByEpisode["proj::1"].map((u) => u.unit_id)).toEqual(["E1U9"]);
    // Invalidated loads discard without resetting; unless writes settle loading, canvases remain stuck.
    expect(state.loading).toBe(false);
  });

  it("loadUnits 的迟到失败不覆盖已接管请求写入的数据", async () => {
    const releases: Array<{ resolve: (units: ReferenceVideoUnit[]) => void; reject: (e: Error) => void }> = [];
    vi.spyOn(API, "listReferenceVideoUnits").mockImplementation(
      () =>
        new Promise((resolve, reject) => {
          releases.push({ resolve: (units) => resolve({ units, unit_capabilities: {} }), reject });
        }),
    );

    await act(async () => {
      const first = useReferenceVideoStore.getState().loadUnits("proj", 1);
      const second = useReferenceVideoStore.getState().loadUnits("proj", 1);
      releases[1].resolve([mkUnit("E1U1")]);
      releases[0].reject(new Error("boom"));
      await Promise.all([first, second]);
    });

    const state = useReferenceVideoStore.getState();
    expect(state.unitsByEpisode["proj::1"]).toHaveLength(1);
    expect(state.error).toBeNull();
  });

  it("addUnit appends unit and selects it", async () => {
    vi.spyOn(API, "addReferenceVideoUnit").mockResolvedValueOnce({
      unit: mkUnit("E1U3"),
      unit_capability: makeReferenceUnitCapability("E1U3", { allowed_durations: [5, 10] }),
    });

    await act(async () => {
      await useReferenceVideoStore.getState().addUnit("proj", 1, { prompt: "new" });
    });

    const state = useReferenceVideoStore.getState();
    expect(state.unitsByEpisode["proj::1"]).toEqual([expect.objectContaining({ unit_id: "E1U3" })]);
    expect(state.unitCapabilitiesByEpisode["proj::1"].E1U3.allowed_durations).toEqual([5, 10]);
    expect(state.selectedUnitId).toBe("E1U3");
  });

  it("patchUnit replaces the unit returned by server", async () => {
    useReferenceVideoStore.setState({
      unitsByEpisode: { "proj::1": [mkUnit("E1U1")] },
      unitCapabilitiesByEpisode: { "proj::1": { E1U1: makeReferenceUnitCapability("E1U1") } },
      selectedUnitId: "E1U1",
      loading: false,
      error: null,
    });
    vi.spyOn(API, "patchReferenceVideoUnit").mockResolvedValueOnce({
      unit: mkUnit("E1U1", { note: "updated" }),
      unit_capability: makeReferenceUnitCapability("E1U1", { hydrated_capability: "r2v", allowed_durations: [8] }),
    });

    await act(async () => {
      await useReferenceVideoStore.getState().patchUnit("proj", 1, "E1U1", { note: "updated" });
    });

    expect(useReferenceVideoStore.getState().unitsByEpisode["proj::1"][0].note).toBe("updated");
    // Body edits may change available references; PATCH responses synchronize buckets/tiers.
    expect(useReferenceVideoStore.getState().unitCapabilitiesByEpisode["proj::1"].E1U1).toMatchObject({
      hydrated_capability: "r2v",
      allowed_durations: [8],
    });
  });

  it("deleteUnit removes unit and clears selection if it was selected", async () => {
    useReferenceVideoStore.setState({
      unitsByEpisode: { "proj::1": [mkUnit("E1U1"), mkUnit("E1U2")] },
      selectedUnitId: "E1U1",
      loading: false,
      error: null,
    });
    vi.spyOn(API, "deleteReferenceVideoUnit").mockResolvedValueOnce(undefined);

    await act(async () => {
      await useReferenceVideoStore.getState().deleteUnit("proj", 1, "E1U1");
    });

    const state = useReferenceVideoStore.getState();
    expect(state.unitsByEpisode["proj::1"].map((u) => u.unit_id)).toEqual(["E1U2"]);
    expect(state.selectedUnitId).toBeNull();
  });

  it("moveUnit replaces episode array with server response", async () => {
    const reordered = [mkUnit("E1U2"), mkUnit("E1U1")];
    vi.spyOn(API, "moveReferenceVideoUnit").mockResolvedValueOnce({ units: reordered });

    await act(async () => {
      await useReferenceVideoStore.getState().moveUnit("proj", 1, "E1U2", null);
    });

    expect(useReferenceVideoStore.getState().unitsByEpisode["proj::1"].map((u) => u.unit_id))
      .toEqual(["E1U2", "E1U1"]);
  });

  it("select sets selectedUnitId", () => {
    useReferenceVideoStore.getState().select("E1U7");
    expect(useReferenceVideoStore.getState().selectedUnitId).toBe("E1U7");
  });

  it("isolates cache across projects with the same episode number", async () => {
    vi.spyOn(API, "listReferenceVideoUnits")
      .mockResolvedValueOnce({ units: [mkUnit("A-E1-U1")], unit_capabilities: {} })
      .mockResolvedValueOnce({ units: [mkUnit("B-E1-U1")], unit_capabilities: {} });

    await act(async () => {
      await useReferenceVideoStore.getState().loadUnits("projA", 1);
    });
    await act(async () => {
      await useReferenceVideoStore.getState().loadUnits("projB", 1);
    });

    const state = useReferenceVideoStore.getState();
    expect(state.unitsByEpisode["projA::1"].map((u) => u.unit_id)).toEqual(["A-E1-U1"]);
    expect(state.unitsByEpisode["projB::1"].map((u) => u.unit_id)).toEqual(["B-E1-U1"]);
  });
});
