import { useState } from "react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { API } from "@/api";
import { ReferenceVideoCard } from "./ReferenceVideoCard";
import { useProjectsStore } from "@/stores/projects-store";
import type { ProjectData } from "@/types";
import type { ReferenceVideoUnit } from "@/types/reference-video";

// Backend shape: one unit body `text`, separate duration.
function mkUnit(overrides: Partial<ReferenceVideoUnit> = {}): ReferenceVideoUnit {
  return {
    unit_id: "E1U1",
    text: "hi",
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

// Parents control prompt; lightweight wrappers reproduce lifted state,
// allowing consecutive userEvent.type keystrokes to accumulate in textarea. Fixed `value`
// would revert every keystroke to initials, invalidating `user.type` assertions.
function ControlledCard({
  unit,
  initial,
  onChange,
}: {
  unit: ReferenceVideoUnit;
  initial?: string;
  onChange?: (next: string) => void;
}) {
  const [val, setVal] = useState(initial ?? unit.text);
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
  characters: { 主角: { description: "" }, 张三: { description: "" }, "角色甲（成年）": { description: "" } },
  scenes: { 酒馆: { description: "" }, "地点甲·版本A": { description: "" } },
  props: { 长剑: { description: "" } },
};

beforeEach(() => {
  useProjectsStore.setState({ currentProjectName: "proj", currentProjectData: PROJECT, assetFingerprints: {} });
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("ReferenceVideoCard", () => {
  // Body is sole truth: display `text` verbatim without synthesized prefixes.
  it("renders the unit body verbatim, multi-line included", () => {
    const unit = mkUnit({ text: "line1\nline2", duration_seconds: 8 });
    render(<ControlledCard unit={unit} />);
    const ta = screen.getByRole("combobox") as HTMLTextAreaElement;
    expect(ta.value).toBe("line1\nline2");
  });

  it("shows the unit source text read-only beside the editor", () => {
    render(<ControlledCard unit={mkUnit({ source_text: "张三推开了门。" })} />);

    const region = screen.getByRole("region", { name: "对应原文" });
    expect(within(region).getByText("张三推开了门。")).toBeInTheDocument();
    expect(within(region).queryByRole("textbox")).not.toBeInTheDocument();
  });

  it("shows an empty source text state for manually added units", () => {
    render(<ControlledCard unit={mkUnit()} />);

    expect(within(screen.getByRole("region", { name: "对应原文" })).getByText("（无对应原文）")).toBeInTheDocument();
  });

  it("highlights inline speech marks in the editor overlay", () => {
    // Highlight mirrors share ScriptHighlight rules: preserve markers verbatim, adding only backgrounds/speaker titles.
    const unit = mkUnit({ text: "门开了。@[张三]：{我来了}", duration_seconds: 3 });
    const { container } = render(<ControlledCard unit={unit} />);
    const dialogue = container.querySelector('[title="张三"]');
    expect(dialogue?.textContent).toBe("@[张三]：{我来了}");

    render(<ControlledCard unit={mkUnit({ text: "夜色渐深。{很久以前……}" })} />);
    const voiceover = document.querySelector('[title="画外音"]');
    expect(voiceover?.textContent).toBe("{很久以前……}");
  });

  it("shows the pending-authoring hint until authoring clears the flag", () => {
    const { rerender } = render(<ControlledCard unit={mkUnit({ text: "", pending_authoring: true })} />);
    expect(screen.getByText(/^待编写/)).toBeInTheDocument();

    rerender(<ControlledCard unit={mkUnit({ text: "", pending_authoring: false })} />);
    expect(screen.queryByText(/^待编写/)).not.toBeInTheDocument();
  });

  it("does not show the pending-authoring hint for units without the flag, even with an empty body", () => {
    render(<ControlledCard unit={mkUnit({ text: "" })} />);
    expect(screen.queryByText(/^待编写/)).not.toBeInTheDocument();
  });

  it("fires onChange with the new prompt text on every edit", async () => {
    const onChange = vi.fn();
    const user = userEvent.setup();
    render(<ControlledCard unit={mkUnit()} onChange={onChange} />);
    const ta = screen.getByRole("combobox");
    await user.clear(ta);
    await user.type(ta, "推门而入。@主角");
    const lastCall = onChange.mock.calls.at(-1)!;
    expect(lastCall[0]).toBe("推门而入。@主角");
    // Cards report bodies only; execution resolves references from `@[名称]`, not inline during editing.
    expect(lastCall).toHaveLength(1);
  });

  // Regression: Chinese punctuation ("。"/"，", etc.) breaks @ scans; never misread "眼@。|" cursors as
  // active mentions opening permanently empty query="。" pickers.
  it("does not open the picker when cursor sits after a punctuation following an orphan '@'", async () => {
    const user = userEvent.setup();
    render(
      <ControlledCard unit={mkUnit({ text: "" })} />,
    );
    const ta = screen.getByRole("combobox");
    await user.clear(ta);
    await user.type(ta, "眼@");
    expect(await screen.findByRole("listbox")).toBeInTheDocument();
    // Abandon this mention, typing Chinese full stop.
    await user.type(ta, "。");
    await waitFor(() =>
      expect(screen.queryByRole("listbox")).not.toBeInTheDocument(),
    );
    // Horizontal cursor arrows must not revive dangling @.
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

  it("keeps the MentionPicker open while typing punctuation inside wrapped mentions", async () => {
    render(<ControlledCard unit={mkUnit()} />);
    const ta = screen.getByRole("combobox") as HTMLTextAreaElement;
    const value = "x @[角色甲（成年";
    fireEvent.change(ta, { target: { value, selectionStart: value.length } });
    expect(await screen.findByRole("option", { name: "角色甲（成年）" })).toBeInTheDocument();
  });

  it("does not treat curly-brace input as a wrapped mention query", async () => {
    render(<ControlledCard unit={mkUnit()} />);
    const ta = screen.getByRole("combobox") as HTMLTextAreaElement;
    const value = "x @{道具甲";
    fireEvent.change(ta, { target: { value, selectionStart: value.length } });
    await waitFor(() =>
      expect(screen.queryByRole("listbox")).not.toBeInTheDocument(),
    );
  });

  it("inserts selected mention into the prompt and closes picker", async () => {
    const onChange = vi.fn();
    const user = userEvent.setup();
    render(
      <ControlledCard
        unit={mkUnit({ text: "" })}
        onChange={onChange}
      />,
    );
    const ta = screen.getByRole("combobox");
    await user.clear(ta);
    await user.type(ta, "@");
    fireEvent.click(await screen.findByRole("option", { name: /主角/ }));
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
    const lastCall = onChange.mock.calls.at(-1)!;
    expect(lastCall[0]).toMatch(/@\[主角\]\s$/);
  });

  it("offers each character form and inserts the derivative reference verbatim", async () => {
    // Derivatives are independent names displayed as base / derivative, inserted as `@[本体/衍生]`.
    useProjectsStore.setState({
      currentProjectName: "proj",
      currentProjectData: {
        ...PROJECT,
        characters: {
          ...PROJECT.characters,
          主角: { description: "", derivatives: { 劲装: { description: "换上黑色劲装" } } },
        },
      },
    });
    const onChange = vi.fn();
    const user = userEvent.setup();
    render(<ControlledCard unit={mkUnit({ text: "" })} onChange={onChange} />);
    const ta = screen.getByRole("combobox");
    await user.clear(ta);
    await user.type(ta, "@劲装");
    fireEvent.click(await screen.findByRole("option", { name: /主角 \/ 劲装/ }));
    expect(onChange.mock.calls.at(-1)![0]).toMatch(/@\[主角\/劲装\]\s$/);
  });

  it("keeps the picker open while typing the derivative separator in the bare form", async () => {
    useProjectsStore.setState({
      currentProjectName: "proj",
      currentProjectData: {
        ...PROJECT,
        characters: {
          ...PROJECT.characters,
          主角: { description: "", derivatives: { 劲装: { description: "换上黑色劲装" } } },
        },
      },
    });
    const user = userEvent.setup();
    render(<ControlledCard unit={mkUnit({ text: "" })} />);
    const ta = screen.getByRole("combobox");
    await user.clear(ta);
    await user.type(ta, "@主角/劲");
    expect(await screen.findByRole("option", { name: /主角 \/ 劲装/ })).toBeInTheDocument();
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

  // Two-step Backspace: select/highlight @mention first, browser delete-selection second.
  it("first Backspace next to a mention selects it; second deletes the whole chip", async () => {
    const user = userEvent.setup();
    const unit = mkUnit({ text: "hi @主角" });
    render(<ControlledCard unit={unit} />);
    const ta = screen.getByRole("combobox") as HTMLTextAreaElement;
    expect(ta.value.endsWith("@主角")).toBe(true);
    ta.focus();
    ta.setSelectionRange(ta.value.length, ta.value.length);

    // First Backspace selects entire @主角 without changing text.
    await user.keyboard("{Backspace}");
    expect(ta.value.endsWith("@主角")).toBe(true);
    expect(ta.selectionStart).toBeLessThan(ta.selectionEnd);
    const selected = ta.value.slice(ta.selectionStart, ta.selectionEnd);
    expect(selected).toBe("@主角");

    // Second Backspace uses default delete-selection for the whole range.
    await user.keyboard("{Backspace}");
    expect(ta.value.endsWith("@主角")).toBe(false);
    expect(ta.value).toMatch(/hi\s*$/);
  });

  it("renders an unknown-mention chip for names not in project", () => {
    render(
      <ControlledCard
        unit={mkUnit({ text: "@路人", duration_seconds: 3 })}
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
    // aria-label is short, not the long placeholder.
    expect(ta).toHaveAttribute("aria-label", "视频单元提示词");

    await user.clear(ta);
    await user.type(ta, "@");
    await waitFor(() => {
      expect(ta).toHaveAttribute("aria-expanded", "true");
    });
    // activedescendant points to first option id, initial activeIndex=0.
    const firstOption = screen.getAllByRole("option")[0];
    expect(firstOption.id).toBeTruthy();
    await waitFor(() => {
      expect(ta).toHaveAttribute("aria-activedescendant", firstOption.id);
    });
  });

  it("wires aria-describedby to unknown-mentions live region", () => {
    const unit = mkUnit({ text: "@未知人 出现" });
    renderCard(unit);
    const ta = screen.getByRole("combobox");
    expect(ta).toHaveAttribute("aria-describedby", "reference-editor-unknown-desc");
    const desc = document.getElementById("reference-editor-unknown-desc");
    expect(desc).not.toBeNull();
    expect(desc).toHaveAttribute("aria-live", "polite");
    expect(desc?.textContent).toContain("未知人");
  });

  it("omits aria-describedby when there are no unknown mentions", () => {
    renderCard(mkUnit()); // default "hi", no mention.
    const ta = screen.getByRole("combobox");
    expect(ta).not.toHaveAttribute("aria-describedby");
  });
});

describe("ReferenceVideoCard final prompt preview", () => {
  it("renders the requested body with numbered request images and warnings", async () => {
    useProjectsStore.setState({ assetFingerprints: { "scenes/酒馆.png": 1712345678901 } });
    const user = userEvent.setup();
    const preview = vi.spyOn(API, "previewReferenceUnitPrompt").mockResolvedValue({
      text: "<酒馆>@图片1。\n草稿正文\n电影质感",
      unavailable: null,
      is_text_form: true,
      references: [{ type: "scene", name: "酒馆", path: "scenes/酒馆.png" }],
      warnings: ["参考图已裁剪"],
    });
    render(<ControlledCard unit={mkUnit({ text: "已保存正文" })} />);
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "未保存草稿" } });
    expect(preview).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "查看提示词" }));
    const dialog = await screen.findByRole("dialog", { name: "参考生视频提示词" });
    expect(await within(dialog).findByText(/草稿正文/)).toBeInTheDocument();
    expect(within(dialog).getByRole("img", { name: "酒馆" })).toHaveAttribute(
      "src",
      API.getFileUrl("proj", "scenes/酒馆.png", 1712345678901, { width: 160 }),
    );
    expect(within(dialog).getByText("图片1 · 酒馆")).toBeInTheDocument();
    expect(within(dialog).getByText("参考图已裁剪")).toBeInTheDocument();
    expect(preview).toHaveBeenCalledWith("proj", 1, "E1U1", "未保存草稿", { signal: expect.any(AbortSignal) });
  });

  it("copies the final prompt and re-renders it on refresh", async () => {
    const user = userEvent.setup();
    const preview = vi.spyOn(API, "previewReferenceUnitPrompt").mockResolvedValue({
      text: "首次渲染的文本",
      unavailable: null,
      is_text_form: true,
      references: [{ type: "scene", name: "酒馆", path: "scenes/酒馆.png" }],
      warnings: [],
    });
    render(<ControlledCard unit={mkUnit({ text: "已保存正文" })} />);
    await user.click(screen.getByRole("button", { name: "查看提示词" }));
    const dialog = await screen.findByRole("dialog", { name: "参考生视频提示词" });
    expect(await within(dialog).findByText("首次渲染的文本")).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "复制最终提示词" }));
    expect(await within(dialog).findByRole("button", { name: /已复制/ })).toBeInTheDocument();
    preview.mockResolvedValueOnce({ text: "重新渲染的文本", unavailable: null, is_text_form: true, references: [], warnings: [] });
    await user.click(within(dialog).getByRole("button", { name: "重新渲染" }));
    expect(await within(dialog).findByText("重新渲染的文本")).toBeInTheDocument();
    expect(within(dialog).getByText("本次请求不携带参考图")).toBeInTheDocument();
  });

  // Prompts are bodies, sharing proportional fonts with highlight mirrors so cursors align.
  it("keeps the prompt editor and its overlay in the proportional font", () => {
    const { container } = render(<ControlledCard unit={mkUnit({ text: "张三推开了门。" })} />);

    expect(screen.getByRole("combobox")).not.toHaveClass("font-mono");
    expect(container.querySelector("pre")).not.toHaveClass("font-mono");
  });
});
