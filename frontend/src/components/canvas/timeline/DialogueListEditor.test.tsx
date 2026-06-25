import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import { DialogueListEditor } from "./DialogueListEditor";
import type { Dialogue } from "@/types";

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string) => key,
    i18n: { language: "en" },
  }),
}));

const dialogue: Dialogue[] = [
  { speaker: "韩青", line: "好险的关隘！田单未能攻破，正面尚未被攻破？" },
];

describe("DialogueListEditor", () => {
  it("renders the line as a textarea so long dialogue wraps instead of clipping", () => {
    render(<DialogueListEditor dialogue={dialogue} onChange={() => {}} />);
    const line = screen.getByDisplayValue(dialogue[0].line);
    expect(line.tagName).toBe("TEXTAREA");
  });

  it("emits the updated line on change", () => {
    const onChange = vi.fn();
    render(<DialogueListEditor dialogue={dialogue} onChange={onChange} />);
    fireEvent.change(screen.getByDisplayValue(dialogue[0].line), {
      target: { value: "新台词" },
    });
    expect(onChange).toHaveBeenCalledWith([{ speaker: "韩青", line: "新台词" }]);
  });

  it("emits the updated speaker on change", () => {
    const onChange = vi.fn();
    render(<DialogueListEditor dialogue={dialogue} onChange={onChange} />);
    fireEvent.change(screen.getByDisplayValue("韩青"), {
      target: { value: "田单" },
    });
    expect(onChange).toHaveBeenCalledWith([
      { speaker: "田单", line: dialogue[0].line },
    ]);
  });

  it("blocks Enter so a dialogue line stays single-line", () => {
    render(<DialogueListEditor dialogue={dialogue} onChange={() => {}} />);
    const prevented = !fireEvent.keyDown(screen.getByDisplayValue(dialogue[0].line), {
      key: "Enter",
    });
    expect(prevented).toBe(true);
  });

  it("lets IME use Enter to commit a candidate", () => {
    render(<DialogueListEditor dialogue={dialogue} onChange={() => {}} />);
    const prevented = !fireEvent.keyDown(screen.getByDisplayValue(dialogue[0].line), {
      key: "Enter",
      isComposing: true,
    });
    expect(prevented).toBe(false);
  });

  it("appends an empty pair on add", () => {
    const onChange = vi.fn();
    render(<DialogueListEditor dialogue={dialogue} onChange={onChange} />);
    fireEvent.click(screen.getByRole("button", { name: "add_dialogue" }));
    expect(onChange).toHaveBeenCalledWith([
      ...dialogue,
      { speaker: "", line: "" },
    ]);
  });

  it("removes a pair on delete", () => {
    const onChange = vi.fn();
    render(<DialogueListEditor dialogue={dialogue} onChange={onChange} />);
    fireEvent.click(screen.getByRole("button", { name: "dialogue_remove" }));
    expect(onChange).toHaveBeenCalledWith([]);
  });
});
