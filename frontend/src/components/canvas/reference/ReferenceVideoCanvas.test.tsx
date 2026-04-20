import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { ReferenceVideoCanvas } from "./ReferenceVideoCanvas";
import { useReferenceVideoStore } from "@/stores/reference-video-store";
import { useProjectsStore } from "@/stores/projects-store";
import { API } from "@/api";
import type { ReferenceVideoUnit } from "@/types";
import type { ProjectData } from "@/types";

function mkUnit(id: string, shotText = "x"): ReferenceVideoUnit {
  return {
    unit_id: id,
    shots: [{ duration: 3, text: shotText }],
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
  };
}

const STUB_PROJECT: ProjectData = {
  title: "p",
  content_mode: "narration",
  style: "",
  episodes: [],
  characters: {},
  scenes: {},
  props: {},
};

describe("ReferenceVideoCanvas", () => {
  beforeEach(() => {
    useReferenceVideoStore.setState({ unitsByEpisode: {}, selectedUnitId: null, loading: false, error: null });
    useProjectsStore.setState({ currentProjectName: "proj", currentProjectData: STUB_PROJECT });
  });
  afterEach(() => vi.restoreAllMocks());

  it("loads units on mount and renders the list", async () => {
    vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({ units: [mkUnit("E1U1"), mkUnit("E1U2")] });
    render(<ReferenceVideoCanvas projectName="proj" episode={1} />);
    await waitFor(() => expect(screen.getByText("E1U1")).toBeInTheDocument());
    expect(screen.getByText("E1U2")).toBeInTheDocument();
  });

  it("selects a unit and shows it in preview panel", async () => {
    vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({ units: [mkUnit("E1U1")] });
    render(<ReferenceVideoCanvas projectName="proj" episode={1} />);
    await waitFor(() => expect(screen.getByText("E1U1")).toBeInTheDocument());
    fireEvent.click(screen.getByTestId("unit-row-E1U1"));
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /Generate video|生成视频/ })).toBeInTheDocument();
    });
  });

  it("renders the ReferenceVideoCard textarea when a unit is selected", async () => {
    vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({
      units: [mkUnit("E1U1")],
    });
    render(<ReferenceVideoCanvas projectName="proj" episode={1} />);
    await waitFor(() => expect(screen.getByText("E1U1")).toBeInTheDocument());
    fireEvent.click(screen.getByTestId("unit-row-E1U1"));
    const ta = await screen.findByRole("combobox");
    expect((ta as HTMLTextAreaElement).value).toContain("Shot 1 (3s): x");
  });

  it("remounts the card so textarea shows the new unit's prompt when selection changes", async () => {
    vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({
      units: [mkUnit("E1U1", "hello from A"), mkUnit("E1U2", "hello from B")],
    });
    render(<ReferenceVideoCanvas projectName="proj" episode={1} />);
    await waitFor(() => expect(screen.getByText("E1U1")).toBeInTheDocument());
    fireEvent.click(screen.getByTestId("unit-row-E1U1"));
    const taA = (await screen.findByRole("combobox")) as HTMLTextAreaElement;
    expect(taA.value).toContain("hello from A");
    fireEvent.click(screen.getByTestId("unit-row-E1U2"));
    await waitFor(() => {
      expect((screen.getByRole("combobox") as HTMLTextAreaElement).value).toContain("hello from B");
    });
  });

  it("adds a new unit via the store when the button is clicked", async () => {
    vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({ units: [] });
    const addSpy = vi.spyOn(API, "addReferenceVideoUnit").mockResolvedValue({ unit: mkUnit("E1U1") });
    render(<ReferenceVideoCanvas projectName="proj" episode={1} />);
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /New Unit|新建 Unit/ })).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByRole("button", { name: /New Unit|新建 Unit/ }));
    await waitFor(() => expect(addSpy).toHaveBeenCalled());
  });
});
