import { beforeEach, describe, expect, it, vi, afterEach } from "vitest";
import { act } from "@testing-library/react";
import { _resetDebounceState, useReferenceVideoStore } from "./reference-video-store";
import { API } from "@/api";
import type { ReferenceVideoUnit } from "@/types";

function mkUnit(id: string, overrides: Partial<ReferenceVideoUnit> = {}): ReferenceVideoUnit {
  return {
    unit_id: id,
    shots: [{ duration: 3, text: "Shot 1 (3s): x" }],
    references: [],
    duration_seconds: 3,
    duration_override: false,
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

  it("addUnit appends unit and selects it", async () => {
    vi.spyOn(API, "addReferenceVideoUnit").mockResolvedValueOnce({ unit: mkUnit("E1U3") });

    await act(async () => {
      await useReferenceVideoStore.getState().addUnit("proj", 1, {
        prompt: "Shot 1 (3s): new",
        references: [],
      });
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

describe("reference-video-store · updatePromptDebounced", () => {
  beforeEach(() => {
    useReferenceVideoStore.setState({
      unitsByEpisode: {},
      selectedUnitId: null,
      loading: false,
      error: null,
    });
    _resetDebounceState();
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("delays network call and writes server response to store", async () => {
    useReferenceVideoStore.setState({
      unitsByEpisode: { "proj::1": [mkUnit("E1U1")] },
      selectedUnitId: "E1U1",
      loading: false,
      error: null,
    });
    const serverUnit = mkUnit("E1U1", { note: "saved" });
    const patchSpy = vi
      .spyOn(API, "patchReferenceVideoUnit")
      .mockResolvedValueOnce({ unit: serverUnit });

    useReferenceVideoStore.getState().updatePromptDebounced("proj", 1, "E1U1", "Shot 1 (3s): x", []);
    expect(patchSpy).not.toHaveBeenCalled();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(600);
    });
    expect(patchSpy).toHaveBeenCalledTimes(1);
    expect(useReferenceVideoStore.getState().unitsByEpisode["proj::1"][0].note).toBe("saved");
  });

  it("coalesces rapid edits into a single network call", async () => {
    useReferenceVideoStore.setState({
      unitsByEpisode: { "proj::1": [mkUnit("E1U1")] },
      selectedUnitId: "E1U1",
      loading: false,
      error: null,
    });
    const patchSpy = vi
      .spyOn(API, "patchReferenceVideoUnit")
      .mockResolvedValue({ unit: mkUnit("E1U1") });

    const store = useReferenceVideoStore.getState();
    store.updatePromptDebounced("proj", 1, "E1U1", "a", []);
    store.updatePromptDebounced("proj", 1, "E1U1", "ab", []);
    store.updatePromptDebounced("proj", 1, "E1U1", "abc", []);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(600);
    });
    expect(patchSpy).toHaveBeenCalledTimes(1);
    const [, , , body] = patchSpy.mock.calls[0]!;
    expect(body).toEqual({ prompt: "abc", references: [] });
  });

  it("discards stale responses when a newer edit races in", async () => {
    useReferenceVideoStore.setState({
      unitsByEpisode: { "proj::1": [mkUnit("E1U1", { note: "original" })] },
      selectedUnitId: "E1U1",
      loading: false,
      error: null,
    });
    let resolveFirst!: (v: { unit: ReferenceVideoUnit }) => void;
    const firstPromise = new Promise<{ unit: ReferenceVideoUnit }>((r) => {
      resolveFirst = r;
    });
    const patchSpy = vi.spyOn(API, "patchReferenceVideoUnit");
    patchSpy.mockReturnValueOnce(firstPromise);
    patchSpy.mockResolvedValueOnce({ unit: mkUnit("E1U1", { note: "v2" }) });

    const store = useReferenceVideoStore.getState();
    store.updatePromptDebounced("proj", 1, "E1U1", "first", []);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(600);
    });
    // first in-flight; now enqueue a second, then let first resolve late
    store.updatePromptDebounced("proj", 1, "E1U1", "second", []);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(600);
    });
    await act(async () => {
      resolveFirst({ unit: mkUnit("E1U1", { note: "v1-late" }) });
      await Promise.resolve();
    });
    expect(useReferenceVideoStore.getState().unitsByEpisode["proj::1"][0].note).toBe("v2");
  });

  it("keeps per-unit timers isolated", async () => {
    useReferenceVideoStore.setState({
      unitsByEpisode: { "proj::1": [mkUnit("E1U1"), mkUnit("E1U2")] },
      selectedUnitId: "E1U1",
      loading: false,
      error: null,
    });
    const patchSpy = vi
      .spyOn(API, "patchReferenceVideoUnit")
      .mockResolvedValueOnce({ unit: mkUnit("E1U1") })
      .mockResolvedValueOnce({ unit: mkUnit("E1U2") });

    const store = useReferenceVideoStore.getState();
    store.updatePromptDebounced("proj", 1, "E1U1", "draft1", []);
    store.updatePromptDebounced("proj", 1, "E1U2", "draft2", []);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(600);
    });
    // Both timers should have fired independently (one per unitId)
    expect(patchSpy).toHaveBeenCalledTimes(2);
  });
});
