import { beforeEach, describe, expect, it, vi, afterEach } from "vitest";
import { act } from "@testing-library/react";
import { useReferenceVideoStore } from "./reference-video-store";
import { API } from "@/api";
import type { ReferenceVideoUnit } from "@/types";

function mkUnit(id: string, overrides: Partial<ReferenceVideoUnit> = {}): ReferenceVideoUnit {
  return {
    unit_id: id,
    text: "x",
    duration_seconds: 3,
    transition_to_next: "cut",
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
    });

    await act(async () => {
      await useReferenceVideoStore.getState().loadUnits("proj", 1);
    });

    const state = useReferenceVideoStore.getState();
    expect(state.unitsByEpisode["proj::1"]).toHaveLength(2);
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
    // 画布 effect 与任务完成失效可能在同一 key 上叠出两次 loadUnits：先发的那次若后返回，
    // 会把新响应里已经生成的成片盖成旧数据，界面停在「无成片」直到下一次失效。
    const releases: Array<(units: ReferenceVideoUnit[]) => void> = [];
    vi.spyOn(API, "listReferenceVideoUnits").mockImplementation(
      () => new Promise((resolve) => releases.push((units) => resolve({ units }))),
    );

    let first: Promise<void>;
    let second: Promise<void>;
    await act(async () => {
      first = useReferenceVideoStore.getState().loadUnits("proj", 1);
      second = useReferenceVideoStore.getState().loadUnits("proj", 1);
      // 后发的先返回（带成片），先发的迟到（无成片）。
      releases[1]([mkUnit("E1U1", { generated_assets: { ...mkUnit("E1U1").generated_assets, video_clip: "v.mp4" } })]);
      releases[0]([mkUnit("E1U1")]);
      await Promise.all([first, second]);
    });

    const units = useReferenceVideoStore.getState().unitsByEpisode["proj::1"];
    expect(units?.[0].generated_assets.video_clip).toBe("v.mp4");
  });

  it("迟到的加载不撤销加载期间落定的增删改排序", async () => {
    // 写入口读到的是写入之后的列表，在途的那次 GET 读的是写入之前的。迟到响应若照样写回，
    // 用户刚做的编辑会在界面上被撤销，直到下一次失效才复原。
    const releases: Array<(units: ReferenceVideoUnit[]) => void> = [];
    vi.spyOn(API, "listReferenceVideoUnits").mockImplementation(
      () => new Promise((resolve) => releases.push((units) => resolve({ units }))),
    );
    vi.spyOn(API, "addReferenceVideoUnit").mockResolvedValueOnce({ unit: mkUnit("E1U9") });

    await act(async () => {
      const load = useReferenceVideoStore.getState().loadUnits("proj", 1);
      await useReferenceVideoStore.getState().addUnit("proj", 1, { prompt: "p" });
      releases[0]([mkUnit("E1U1")]);
      await load;
    });

    const state = useReferenceVideoStore.getState();
    expect(state.unitsByEpisode["proj::1"].map((u) => u.unit_id)).toEqual(["E1U9"]);
    // 被作废的那次加载直接丢弃、不再自己复位，写入口不结算的话画布会一直停在加载态。
    expect(state.loading).toBe(false);
  });

  it("loadUnits 的迟到失败不覆盖已接管请求写入的数据", async () => {
    const releases: Array<{ resolve: (units: ReferenceVideoUnit[]) => void; reject: (e: Error) => void }> = [];
    vi.spyOn(API, "listReferenceVideoUnits").mockImplementation(
      () =>
        new Promise((resolve, reject) => {
          releases.push({ resolve: (units) => resolve({ units }), reject });
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
    vi.spyOn(API, "addReferenceVideoUnit").mockResolvedValueOnce({ unit: mkUnit("E1U3") });

    await act(async () => {
      await useReferenceVideoStore.getState().addUnit("proj", 1, { prompt: "new" });
    });

    const state = useReferenceVideoStore.getState();
    expect(state.unitsByEpisode["proj::1"]).toEqual([expect.objectContaining({ unit_id: "E1U3" })]);
    expect(state.selectedUnitId).toBe("E1U3");
  });

  it("patchUnit replaces the unit returned by server", async () => {
    useReferenceVideoStore.setState({
      unitsByEpisode: { "proj::1": [mkUnit("E1U1")] },
      selectedUnitId: "E1U1",
      loading: false,
      error: null,
    });
    vi.spyOn(API, "patchReferenceVideoUnit").mockResolvedValueOnce({
      unit: mkUnit("E1U1", { note: "updated" }),
    });

    await act(async () => {
      await useReferenceVideoStore.getState().patchUnit("proj", 1, "E1U1", { note: "updated" });
    });

    expect(useReferenceVideoStore.getState().unitsByEpisode["proj::1"][0].note).toBe("updated");
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

  it("reorderUnits replaces episode array with server response", async () => {
    const reordered = [mkUnit("E1U2"), mkUnit("E1U1")];
    vi.spyOn(API, "reorderReferenceVideoUnits").mockResolvedValueOnce({ units: reordered });

    await act(async () => {
      await useReferenceVideoStore.getState().reorderUnits("proj", 1, ["E1U2", "E1U1"]);
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
      .mockResolvedValueOnce({ units: [mkUnit("A-E1-U1")] })
      .mockResolvedValueOnce({ units: [mkUnit("B-E1-U1")] });

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
