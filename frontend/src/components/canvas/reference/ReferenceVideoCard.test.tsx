import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ReferenceVideoCard } from "./ReferenceVideoCard";
import { useProjectsStore } from "@/stores/projects-store";
import type { ProjectData } from "@/types";
import type { ReferenceVideoUnit } from "@/types/reference-video";

function mkUnit(overrides: Partial<ReferenceVideoUnit> = {}): ReferenceVideoUnit {
  return {
    unit_id: "E1U1",
    shots: [{ duration: 3, text: "Shot 1 (3s): hi" }],
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

const PROJECT: ProjectData = {
  title: "p",
  content_mode: "narration",
  style: "",
  episodes: [],
  characters: { 主角: { description: "" }, 张三: { description: "" } },
  scenes: { 酒馆: { description: "" } },
  props: { 长剑: { description: "" } },
};

beforeEach(() => {
  useProjectsStore.setState({ currentProjectName: "proj", currentProjectData: PROJECT });
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("ReferenceVideoCard", () => {
  it("renders the unit's joined shot text in the textarea", () => {
    const unit = mkUnit({
      shots: [
        { duration: 3, text: "Shot 1 (3s): line1" },
        { duration: 5, text: "Shot 2 (5s): line2" },
      ],
    });
    render(
      <ReferenceVideoCard
        unit={unit}
        projectName="proj"
        episode={1}
        onChangePrompt={vi.fn()}
      />,
    );
    const ta = screen.getByRole("textbox") as HTMLTextAreaElement;
    expect(ta.value).toContain("Shot 1 (3s): line1");
    expect(ta.value).toContain("Shot 2 (5s): line2");
  });

  it("fires onChangePrompt with (prompt, merged references) on every edit", async () => {
    const onChange = vi.fn();
    const user = userEvent.setup();
    render(
      <ReferenceVideoCard
        unit={mkUnit()}
        projectName="proj"
        episode={1}
        onChangePrompt={onChange}
      />,
    );
    const ta = screen.getByRole("textbox");
    await user.clear(ta);
    await user.type(ta, "Shot 1 (3s): @主角");
    const lastCall = onChange.mock.calls.at(-1)!;
    expect(lastCall[0]).toBe("Shot 1 (3s): @主角");
    expect(lastCall[1]).toEqual([{ type: "character", name: "主角" }]);
  });

  it("opens the MentionPicker when '@' is typed", async () => {
    const user = userEvent.setup();
    render(
      <ReferenceVideoCard
        unit={mkUnit()}
        projectName="proj"
        episode={1}
        onChangePrompt={vi.fn()}
      />,
    );
    const ta = screen.getByRole("textbox");
    await user.clear(ta);
    await user.type(ta, "x @");
    expect(await screen.findByRole("listbox")).toBeInTheDocument();
  });

  it("inserts selected mention into the prompt and closes picker", async () => {
    const onChange = vi.fn();
    const user = userEvent.setup();
    render(
      <ReferenceVideoCard
        unit={mkUnit({ shots: [{ duration: 1, text: "" }] })}
        projectName="proj"
        episode={1}
        onChangePrompt={onChange}
      />,
    );
    const ta = screen.getByRole("textbox");
    await user.clear(ta);
    await user.type(ta, "@");
    fireEvent.click(await screen.findByRole("option", { name: /主角/ }));
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
    const lastCall = onChange.mock.calls.at(-1)!;
    expect(lastCall[0]).toMatch(/@主角\s$/);
  });

  it("renders an unknown-mention chip for names not in project", () => {
    render(
      <ReferenceVideoCard
        unit={mkUnit({ shots: [{ duration: 1, text: "Shot 1 (3s): @路人" }] })}
        projectName="proj"
        episode={1}
        onChangePrompt={vi.fn()}
      />,
    );
    const chip = screen.getByRole("status");
    expect(chip).toHaveTextContent(/路人/);
    expect(chip).toHaveTextContent(/未注册|Unregistered/);
  });
});
