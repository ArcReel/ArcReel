import { act, fireEvent, render, renderHook, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { API } from "@/api";
import type { EpisodesView } from "@/types";

import { useManualSplit } from "./useManualSplit";

// a.txt：未切分 [0, 30)
const view: EpisodesView = {
  unit: "chars",
  units: 0,
  cut_units: 0,
  episodes: [],
  unregistered: [],
  replan: null,
  files: [
    {
      source_file: "source/a.txt",
      name: "a.txt",
      original_filename: null,
      missing: false,
      length: 30,
      units: 0,
      cut_units: 0,
      segments: [{ kind: "unsplit", start: 0, end: 30, text: "", episode: null, gap: false, units: 0 }],
      source_kind: null,
    },
  ],
};

function placed() {
  const hook = renderHook(() => useManualSplit("p", view, () => {}));
  act(() => hook.result.current.place({ file: 0, offset: 12 }));
  return hook;
}

function mount<T extends HTMLElement>(element: T): T {
  document.body.append(element);
  return element;
}

describe("useManualSplit keyboard", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    document.body.replaceChildren();
  });

  it("confirms the pending cut on Enter and nudges it with the arrow keys", () => {
    const split = vi.spyOn(API, "manualSplit").mockReturnValue(new Promise(() => {}));
    const hook = placed();

    act(() => {
      fireEvent.keyDown(document.body, { key: "ArrowRight", shiftKey: true });
    });
    expect(hook.result.current.pending).toEqual({ file: 0, offset: 22 });

    act(() => {
      fireEvent.keyDown(document.body, { key: "Enter" });
    });
    expect(split).toHaveBeenCalledWith("p", expect.objectContaining({ action: "cut", end: 22 }), {});
  });

  it("leaves Enter and the arrow keys to a focused button or select", () => {
    const split = vi.spyOn(API, "manualSplit");
    const hook = placed();
    const button = mount(document.createElement("button"));
    const select = mount(document.createElement("select"));

    act(() => {
      fireEvent.keyDown(button, { key: "Enter" });
      fireEvent.keyDown(select, { key: "ArrowRight" });
    });

    expect(split).not.toHaveBeenCalled();
    expect(hook.result.current.pending).toEqual({ file: 0, offset: 12 });
  });

  it("confirms on Enter in the toolbar's title field", () => {
    const split = vi.spyOn(API, "manualSplit").mockReturnValue(new Promise(() => {}));
    placed();
    const toolbar = mount(document.createElement("span"));
    toolbar.setAttribute("data-manual-split-toolbar", "");
    const input = document.createElement("input");
    toolbar.append(input);

    act(() => {
      fireEvent.keyDown(input, { key: "Enter" });
    });

    expect(split).toHaveBeenCalledTimes(1);
  });
});

describe("useManualSplit confirmation", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  function MergeHarness() {
    const split = useManualSplit("p", view, () => {});
    return (
      <>
        <button type="button" onClick={() => split.mergeWithNext(1)}>
          merge
        </button>
        {split.dialog}
      </>
    );
  }

  it("resubmits a merge with the volume of unsplit text the dialog stated", async () => {
    const split = vi
      .spyOn(API, "manualSplit")
      .mockResolvedValueOnce({
        status: "confirmation_required",
        impact: { restaled: [], retired: [], removed: [2], merged_units: 5, text: "两集之间有 5 字未切分的原文" },
      })
      .mockReturnValueOnce(new Promise(() => {}));
    render(<MergeHarness />);

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "merge" }));
    });
    expect(screen.getByText("两集之间有 5 字未切分的原文")).toBeInTheDocument();
    act(() => {
      fireEvent.click(screen.getByRole("button", { name: "确认调整" }));
    });

    expect(split).toHaveBeenLastCalledWith(
      "p",
      { action: "merge_next", episode: 1 },
      { confirmEpisodes: [], confirmMergedUnits: 5 },
    );
  });
});
