import { useState } from "react";
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { UtteranceListEditor } from "./UtteranceListEditor";
import type { Utterance } from "@/types";

const sample: Utterance[] = [
  { kind: "voiceover", speaker: null, text: "三年后。" },
  { kind: "dialogue", speaker: "阿离", text: "你终于回来了。" },
];

/** Controlled host writes onChange into state, reproducing real delete/move rerenders. */
function Harness({ initial }: { initial: Utterance[] }) {
  const [utterances, setUtterances] = useState(initial);
  return <UtteranceListEditor utterances={utterances} onChange={setUtterances} />;
}

describe("UtteranceListEditor", () => {
  it("renders dialogue with a speaker and voiceover without one, in order", () => {
    render(<UtteranceListEditor utterances={sample} onChange={() => {}} />);
    expect(screen.getByDisplayValue("阿离")).toBeInTheDocument();
    expect(screen.getByDisplayValue("三年后。")).toBeInTheDocument();
    expect(screen.getByDisplayValue("你终于回来了。")).toBeInTheDocument();
    // Only dialogue has speaker inputs, so one speaker field.
    expect(screen.getAllByPlaceholderText("角色")).toHaveLength(1);
  });

  it("adds a dialogue and a voiceover utterance", () => {
    const onChange = vi.fn();
    render(<UtteranceListEditor utterances={sample} onChange={onChange} />);

    fireEvent.click(screen.getByText("添加台词"));
    expect(onChange).toHaveBeenLastCalledWith([...sample, { kind: "dialogue", speaker: "", text: "" }]);

    fireEvent.click(screen.getByText("添加画外音"));
    expect(onChange).toHaveBeenLastCalledWith([...sample, { kind: "voiceover", speaker: null, text: "" }]);
  });

  it("removes an utterance", () => {
    const onChange = vi.fn();
    render(<UtteranceListEditor utterances={sample} onChange={onChange} />);
    fireEvent.click(screen.getAllByLabelText("删除发声")[0]);
    expect(onChange).toHaveBeenCalledWith([sample[1]]);
  });

  it("reorders utterances with move down", () => {
    const onChange = vi.fn();
    render(<UtteranceListEditor utterances={sample} onChange={onChange} />);
    fireEvent.click(screen.getAllByLabelText("下移")[0]);
    expect(onChange).toHaveBeenCalledWith([sample[1], sample[0]]);
  });

  it("toggling a voiceover to dialogue opens an empty speaker (kind ⇄ speaker)", () => {
    const onChange = vi.fn();
    render(<UtteranceListEditor utterances={sample} onChange={onChange} />);
    // First entry is voiceover, with voiceover type-button wording.
    fireEvent.click(screen.getByText("画外音"));
    expect(onChange).toHaveBeenCalledWith([
      { kind: "dialogue", speaker: "", text: "三年后。" },
      sample[1],
    ]);
  });

  it("editing a dialogue speaker preserves kind and text", () => {
    const onChange = vi.fn();
    render(<UtteranceListEditor utterances={sample} onChange={onChange} />);
    fireEvent.change(screen.getByDisplayValue("阿离"), { target: { value: "阿离 " } });
    expect(onChange).toHaveBeenCalledWith([
      sample[0],
      { kind: "dialogue", speaker: "阿离 ", text: "你终于回来了。" },
    ]);
  });

  it("shows an empty hint when there are no utterances", () => {
    render(<UtteranceListEditor utterances={[]} onChange={() => {}} />);
    expect(screen.getByText("本分镜暂无发声内容。")).toBeInTheDocument();
  });

  // Stable-key regression: array-index keys reuse controlled inputs positionally after delete/move,
  // shifting focus/content to incorrect rows. Assertions keep focus following original entries.
  it("keeps focus on a later row after an earlier row is removed", () => {
    const rows: Utterance[] = [
      { kind: "voiceover", speaker: null, text: "首行画外音。" },
      { kind: "dialogue", speaker: "阿离", text: "中间台词。" },
      { kind: "voiceover", speaker: null, text: "末行画外音。" },
    ];
    render(<Harness initial={rows} />);

    const lastRow = screen.getByDisplayValue("末行画外音。") as HTMLTextAreaElement;
    lastRow.focus();
    expect(lastRow).toHaveFocus();

    fireEvent.click(screen.getAllByLabelText("删除发声")[0]);

    // Last entry/content remain unchanged, focused on the same node rather than an advanced replacement.
    const stillLast = screen.getByDisplayValue("末行画外音。");
    expect(stillLast).toHaveFocus();
    expect(screen.queryByDisplayValue("首行画外音。")).not.toBeInTheDocument();
  });

  it("keeps a moved row's focus and content bound to the same row", () => {
    const rows: Utterance[] = [
      { kind: "voiceover", speaker: null, text: "首行画外音。" },
      { kind: "dialogue", speaker: "阿离", text: "中间台词。" },
      { kind: "voiceover", speaker: null, text: "末行画外音。" },
    ];
    render(<Harness initial={rows} />);

    const lastRow = screen.getByDisplayValue("末行画外音。") as HTMLTextAreaElement;
    lastRow.focus();

    // Moving the last entry up carries content/focus, never replacing with neighbor content.
    fireEvent.click(screen.getAllByLabelText("上移")[2]);

    const focused = document.activeElement as HTMLTextAreaElement;
    expect(focused.value).toBe("末行画外音。");
  });
});
