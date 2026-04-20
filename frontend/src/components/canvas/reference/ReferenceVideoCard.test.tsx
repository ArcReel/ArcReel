import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ReferenceVideoCard } from "./ReferenceVideoCard";
import { useProjectsStore } from "@/stores/projects-store";
import type { ProjectData } from "@/types";
import type { ReferenceVideoUnit } from "@/types/reference-video";

// Shapes match backend: parse_prompt strips the `Shot N (Xs):` header when it
// saves shots[].text, and sets duration_override=true for header-less single
// shots. Keep test mocks aligned so the Card's header reconstruction runs
// against realistic data.
function mkUnit(overrides: Partial<ReferenceVideoUnit> = {}): ReferenceVideoUnit {
  return {
    unit_id: "E1U1",
    shots: [{ duration: 3, text: "hi" }],
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
  it("reconstructs `Shot N (Xs):` headers around each shot's stored text", () => {
    const unit = mkUnit({
      shots: [
        { duration: 3, text: "line1" },
        { duration: 5, text: "line2" },
      ],
      duration_seconds: 8,
      duration_override: false,
    });
    render(
      <ReferenceVideoCard
        unit={unit}
        projectName="proj"
        episode={1}
        onChangePrompt={vi.fn()}
      />,
    );
    const ta = screen.getByRole("combobox") as HTMLTextAreaElement;
    expect(ta.value).toBe("Shot 1 (3s): line1\nShot 2 (5s): line2");
  });

  it("renders raw text (no synthesized header) when duration_override is true", () => {
    const unit = mkUnit({
      shots: [{ duration: 1, text: "plain text with no header" }],
      duration_seconds: 1,
      duration_override: true,
    });
    render(
      <ReferenceVideoCard
        unit={unit}
        projectName="proj"
        episode={1}
        onChangePrompt={vi.fn()}
      />,
    );
    const ta = screen.getByRole("combobox") as HTMLTextAreaElement;
    expect(ta.value).toBe("plain text with no header");
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
    const ta = screen.getByRole("combobox");
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
    const ta = screen.getByRole("combobox");
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
    const ta = screen.getByRole("combobox");
    await user.clear(ta);
    await user.type(ta, "@");
    fireEvent.click(await screen.findByRole("option", { name: /主角/ }));
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
    const lastCall = onChange.mock.calls.at(-1)!;
    expect(lastCall[0]).toMatch(/@主角\s$/);
  });

  it("closes the picker synchronously on textarea blur", async () => {
    const user = userEvent.setup();
    render(
      <ReferenceVideoCard
        unit={mkUnit()}
        projectName="proj"
        episode={1}
        onChangePrompt={vi.fn()}
      />,
    );
    const ta = screen.getByRole("combobox");
    await user.clear(ta);
    await user.type(ta, "@");
    expect(await screen.findByRole("listbox")).toBeInTheDocument();
    ta.blur();
    // mousedown preventDefault on options keeps the textarea focused through
    // clicks, so genuine blurs can close the picker without a setTimeout.
    // No artificial delay — only wait for React's state flush.
    await waitFor(() =>
      expect(screen.queryByRole("listbox")).not.toBeInTheDocument(),
    );
  });

  it("renders an unknown-mention chip for names not in project", () => {
    render(
      <ReferenceVideoCard
        unit={mkUnit({
          shots: [{ duration: 3, text: "@路人" }],
          duration_seconds: 3,
          duration_override: false,
        })}
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

describe("ReferenceVideoCard combobox ARIA", () => {
  function renderCard(unit = mkUnit()) {
    return render(
      <ReferenceVideoCard
        unit={unit}
        projectName="proj"
        episode={1}
        onChangePrompt={vi.fn()}
      />,
    );
  }

  it("advertises combobox contract before and after picker opens", async () => {
    const user = userEvent.setup();
    renderCard();
    const ta = screen.getByRole("combobox");
    expect(ta).toHaveAttribute("aria-expanded", "false");
    expect(ta).toHaveAttribute("aria-controls", "reference-editor-picker");
    expect(ta).toHaveAttribute("aria-autocomplete", "list");
    // aria-label 是短名，不是长 placeholder
    expect(ta.getAttribute("aria-label")).toBe("Unit 提示词");

    await user.clear(ta);
    await user.type(ta, "@");
    await waitFor(() => {
      expect(ta).toHaveAttribute("aria-expanded", "true");
    });
    // activedescendant 指向第一个 option 的 id（初始 activeIndex=0）
    const firstOption = screen.getAllByRole("option")[0];
    expect(firstOption.id).toBeTruthy();
    await waitFor(() => {
      expect(ta).toHaveAttribute("aria-activedescendant", firstOption.id);
    });
  });

  it("wires aria-describedby to unknown-mentions live region", () => {
    const unit = mkUnit({
      shots: [{ duration: 3, text: "@未知人 出现" }],
      duration_override: false,
    });
    renderCard(unit);
    const ta = screen.getByRole("combobox");
    expect(ta).toHaveAttribute("aria-describedby", "reference-editor-unknown-desc");
    const desc = document.getElementById("reference-editor-unknown-desc");
    expect(desc).not.toBeNull();
    expect(desc).toHaveAttribute("aria-live", "polite");
    expect(desc?.textContent).toContain("未知人");
  });

  it("omits aria-describedby when there are no unknown mentions", () => {
    renderCard(mkUnit()); // default: "hi"，无 mention
    const ta = screen.getByRole("combobox");
    expect(ta).not.toHaveAttribute("aria-describedby");
  });
});
