import { useState } from "react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ReferenceVideoCard, unitPromptText } from "./ReferenceVideoCard";
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

// 父组件把 prompt 当作受控值传入；用一个轻量 wrapper 在测试中模拟 lifted 状态，
// 这样 userEvent.type 产生的连续击键能在 textarea 上累计。否则 `value` prop 固定，
// 每次 keystroke 都会被 React 回退到初值，`user.type` 的断言就不成立。
function ControlledCard({
  unit,
  initial,
  onChange,
}: {
  unit: ReferenceVideoUnit;
  initial?: string;
  onChange?: (next: string) => void;
}) {
  const [val, setVal] = useState(initial ?? unitPromptText(unit));
  return (
    <ReferenceVideoCard
      unit={unit}
      projectName="proj"
      episode={1}
      value={val}
      onChange={(next) => {
        setVal(next);
        onChange?.(next);
      }}
    />
  );
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
    render(<ControlledCard unit={unit} />);
    const ta = screen.getByRole("combobox") as HTMLTextAreaElement;
    expect(ta.value).toBe("Shot 1 (3s): line1\nShot 2 (5s): line2");
  });

  it("renders raw text (no synthesized header) when duration_override is true", () => {
    const unit = mkUnit({
      shots: [{ duration: 1, text: "plain text with no header" }],
      duration_seconds: 1,
      duration_override: true,
    });
    render(<ControlledCard unit={unit} />);
    const ta = screen.getByRole("combobox") as HTMLTextAreaElement;
    expect(ta.value).toBe("plain text with no header");
  });

  it("fires onChange with the new prompt text on every edit", async () => {
    const onChange = vi.fn();
    const user = userEvent.setup();
    render(<ControlledCard unit={mkUnit()} onChange={onChange} />);
    const ta = screen.getByRole("combobox");
    await user.clear(ta);
    await user.type(ta, "Shot 1 (3s): @主角");
    const lastCall = onChange.mock.calls.at(-1)!;
    expect(lastCall[0]).toBe("Shot 1 (3s): @主角");
    // 新契约：Card 不再做 references 合并——那一步延后到保存时由父组件处理。
    expect(lastCall).toHaveLength(1);
  });

  // 回归：扫 @ 时遇到中文标点（"。"/"，"等）应 break，不能把"眼@。|"的光标误识为
  // 正在输入的 mention，否则会弹出 query="。" 的永远空 picker。
  it("does not open the picker when cursor sits after a punctuation following an orphan '@'", async () => {
    const user = userEvent.setup();
    render(
      <ControlledCard unit={mkUnit({ shots: [{ duration: 1, text: "" }] })} />,
    );
    const ta = screen.getByRole("combobox");
    await user.clear(ta);
    await user.type(ta, "眼@");
    expect(await screen.findByRole("listbox")).toBeInTheDocument();
    // 放弃这次 mention，输入中文句号
    await user.type(ta, "。");
    await waitFor(() =>
      expect(screen.queryByRole("listbox")).not.toBeInTheDocument(),
    );
    // 方向键左右移动光标，也不应"复活"那个悬挂的 @
    await user.keyboard("{ArrowLeft}{ArrowRight}");
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
  });

  it("opens the MentionPicker when '@' is typed", async () => {
    const user = userEvent.setup();
    render(<ControlledCard unit={mkUnit()} />);
    const ta = screen.getByRole("combobox");
    await user.clear(ta);
    await user.type(ta, "x @");
    expect(await screen.findByRole("listbox")).toBeInTheDocument();
  });

  it("inserts selected mention into the prompt and closes picker", async () => {
    const onChange = vi.fn();
    const user = userEvent.setup();
    render(
      <ControlledCard
        unit={mkUnit({ shots: [{ duration: 1, text: "" }] })}
        onChange={onChange}
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
    render(<ControlledCard unit={mkUnit()} />);
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

  // Backspace 两次删除：第一次高亮整个 @mention，第二次由默认 delete-selection 完成删除。
  it("first Backspace next to a mention selects it; second deletes the whole chip", async () => {
    const user = userEvent.setup();
    const unit = mkUnit({
      shots: [{ duration: 3, text: "hi @主角" }],
      duration_override: false,
    });
    render(<ControlledCard unit={unit} />);
    const ta = screen.getByRole("combobox") as HTMLTextAreaElement;
    // 初始值：同 unitPromptText 重构后的 "Shot 1 (3s): hi @主角"
    expect(ta.value.endsWith("@主角")).toBe(true);
    ta.focus();
    ta.setSelectionRange(ta.value.length, ta.value.length);

    // 第一次 Backspace：选中整个 @主角，不变更文本
    await user.keyboard("{Backspace}");
    expect(ta.value.endsWith("@主角")).toBe(true);
    expect(ta.selectionStart).toBeLessThan(ta.selectionEnd);
    const selected = ta.value.slice(ta.selectionStart, ta.selectionEnd);
    expect(selected).toBe("@主角");

    // 第二次 Backspace：浏览器默认 delete-selection 行为删除整个选区
    await user.keyboard("{Backspace}");
    expect(ta.value.endsWith("@主角")).toBe(false);
    expect(ta.value).toMatch(/hi\s*$/);
  });

  it("renders an unknown-mention chip for names not in project", () => {
    render(
      <ControlledCard
        unit={mkUnit({
          shots: [{ duration: 3, text: "@路人" }],
          duration_seconds: 3,
          duration_override: false,
        })}
      />,
    );
    const chip = screen.getByRole("status");
    expect(chip).toHaveTextContent(/路人/);
    expect(chip).toHaveTextContent(/未注册|Unregistered/);
  });
});

describe("ReferenceVideoCard combobox ARIA", () => {
  function renderCard(unit = mkUnit()) {
    return render(<ControlledCard unit={unit} />);
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
