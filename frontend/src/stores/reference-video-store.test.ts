import { beforeEach, describe, expect, it, vi, afterEach } from "vitest";
import { act } from "@testing-library/react";
import { _resetDebounceState, useReferenceVideoStore } from "./reference-video-store";
import { useAppStore } from "./app-store";
import { API } from "@/api";
import type { ReferenceResource, ReferenceVideoUnit } from "@/types";

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

  const REFS_A: ReferenceResource[] = [{ type: "character", name: "主角" }];
  const REFS_B: ReferenceResource[] = [
    { type: "character", name: "主角" },
    { type: "scene", name: "酒馆" },
  ];

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

    useReferenceVideoStore.getState().updatePromptDebounced("proj", 1, "E1U1", "Shot 1 (3s): x", REFS_A);
    expect(patchSpy).not.toHaveBeenCalled();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(600);
    });
    expect(patchSpy).toHaveBeenCalledTimes(1);
    const [, , , body] = patchSpy.mock.calls[0]!;
    expect(body).toEqual({ prompt: "Shot 1 (3s): x", references: REFS_A });
    expect(useReferenceVideoStore.getState().unitsByEpisode["proj::1"][0].note).toBe("saved");
  });

  it("coalesces rapid edits — latest prompt and references win", async () => {
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
    store.updatePromptDebounced("proj", 1, "E1U1", "ab", REFS_A);
    store.updatePromptDebounced("proj", 1, "E1U1", "abc", REFS_B);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(600);
    });
    expect(patchSpy).toHaveBeenCalledTimes(1);
    const [, , , body] = patchSpy.mock.calls[0]!;
    expect(body).toEqual({ prompt: "abc", references: REFS_B });
  });

  it("protects against add-then-remove mention races via latest-payload-wins", async () => {
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
    store.updatePromptDebounced("proj", 1, "E1U1", "@主角", REFS_A);
    store.updatePromptDebounced("proj", 1, "E1U1", "", []);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(600);
    });
    expect(patchSpy).toHaveBeenCalledTimes(1);
    const [, , , body] = patchSpy.mock.calls[0]!;
    expect(body).toEqual({ prompt: "", references: [] });
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

  it("isolates debounce state across projects with the same episode+unitId", async () => {
    useReferenceVideoStore.setState({
      unitsByEpisode: {
        "projA::1": [mkUnit("E1U1", { note: "A-orig" })],
        "projB::1": [mkUnit("E1U1", { note: "B-orig" })],
      },
      selectedUnitId: null,
      loading: false,
      error: null,
    });
    const patchSpy = vi
      .spyOn(API, "patchReferenceVideoUnit")
      .mockImplementation(async (project, _episode, unitId) => ({
        unit: mkUnit(unitId, { note: `${project}-saved` }),
      }));

    const store = useReferenceVideoStore.getState();
    // Edits to projA/E1U1 and projB/E1U1 must not collapse into a single timer.
    store.updatePromptDebounced("projA", 1, "E1U1", "draft-A", []);
    store.updatePromptDebounced("projB", 1, "E1U1", "draft-B", []);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(600);
    });

    expect(patchSpy).toHaveBeenCalledTimes(2);
    const state = useReferenceVideoStore.getState();
    expect(state.unitsByEpisode["projA::1"][0].note).toBe("projA-saved");
    expect(state.unitsByEpisode["projB::1"][0].note).toBe("projB-saved");
  });

  it("consumePendingPrompt returns and clears the queued prompt, canceling the timer", async () => {
    useReferenceVideoStore.setState({
      unitsByEpisode: { "proj::1": [mkUnit("E1U1")] },
      selectedUnitId: "E1U1",
      loading: false,
      error: null,
    });
    const patchSpy = vi.spyOn(API, "patchReferenceVideoUnit");

    const store = useReferenceVideoStore.getState();
    store.updatePromptDebounced("proj", 1, "E1U1", "draft-in-progress", REFS_A);

    const consumed = store.consumePendingPrompt("proj", 1, "E1U1");
    expect(consumed).toBe("draft-in-progress");

    // Second consume returns undefined because the queue was cleared.
    expect(store.consumePendingPrompt("proj", 1, "E1U1")).toBeUndefined();

    // No PATCH should fire from the canceled debounce.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(600);
    });
    expect(patchSpy).not.toHaveBeenCalled();
  });

  it("consumePendingPrompt invalidates in-flight PATCH so its response cannot overwrite later writes", async () => {
    useReferenceVideoStore.setState({
      unitsByEpisode: { "proj::1": [mkUnit("E1U1", { note: "initial" })] },
      selectedUnitId: "E1U1",
      loading: false,
      error: null,
    });
    let resolveFlight!: (v: { unit: ReferenceVideoUnit }) => void;
    const flightPromise = new Promise<{ unit: ReferenceVideoUnit }>((r) => {
      resolveFlight = r;
    });
    vi.spyOn(API, "patchReferenceVideoUnit").mockReturnValueOnce(flightPromise);

    const store = useReferenceVideoStore.getState();
    store.updatePromptDebounced("proj", 1, "E1U1", "debounced", []);
    // Let the timer fire — the PATCH is now in flight.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(600);
    });

    // Caller consumes (mimicking a Panel ref operation) and invalidates the
    // in-flight generation. pendingPayload is already empty because the timer
    // drained it, so undefined is returned.
    expect(store.consumePendingPrompt("proj", 1, "E1U1")).toBeUndefined();

    // Late-arriving response must NOT mutate store state.
    await act(async () => {
      resolveFlight({ unit: mkUnit("E1U1", { note: "stale-response" }) });
      await Promise.resolve();
    });
    expect(useReferenceVideoStore.getState().unitsByEpisode["proj::1"][0].note).toBe("initial");
  });

  it("deleteUnit cancels pending debounce so no PATCH fires after delete", async () => {
    useReferenceVideoStore.setState({
      unitsByEpisode: { "proj::1": [mkUnit("E1U1")] },
      selectedUnitId: "E1U1",
      loading: false,
      error: null,
    });
    const patchSpy = vi.spyOn(API, "patchReferenceVideoUnit");
    const deleteSpy = vi.spyOn(API, "deleteReferenceVideoUnit").mockResolvedValue(undefined);

    const store = useReferenceVideoStore.getState();
    store.updatePromptDebounced("proj", 1, "E1U1", "pending-edit", []);
    await act(async () => {
      await store.deleteUnit("proj", 1, "E1U1");
      await vi.advanceTimersByTimeAsync(600);
    });
    expect(deleteSpy).toHaveBeenCalledTimes(1);
    expect(patchSpy).not.toHaveBeenCalled();
  });
});

describe("updatePromptDebounced failure surfacing (#341)", () => {
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

  it("pushes a toast when the debounced PATCH rejects", async () => {
    const pushToast = vi.spyOn(useAppStore.getState(), "pushToast");
    vi.spyOn(API, "patchReferenceVideoUnit").mockRejectedValueOnce(
      new Error("network down"),
    );

    useReferenceVideoStore
      .getState()
      .updatePromptDebounced("p", 1, "E1U1", "Shot 1 (3s): foo", []);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(600);
    });
    // Flush rejection microtasks.
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();

    expect(pushToast).toHaveBeenCalledWith(
      expect.stringContaining("network down"),
      "error",
    );
  });
});
