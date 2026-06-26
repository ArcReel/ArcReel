import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { UtteranceListEditor } from "./UtteranceListEditor";
import type { Utterance } from "@/types";

const sample: Utterance[] = [
  { kind: "voiceover", speaker: null, text: "三年后。" },
  { kind: "dialogue", speaker: "阿离", text: "你终于回来了。" },
];

describe("UtteranceListEditor", () => {
  it("renders dialogue with a speaker and voiceover without one, in order", () => {
    render(<UtteranceListEditor utterances={sample} onChange={() => {}} />);
    expect(screen.getByDisplayValue("阿离")).toBeInTheDocument();
    expect(screen.getByDisplayValue("三年后。")).toBeInTheDocument();
    expect(screen.getByDisplayValue("你终于回来了。")).toBeInTheDocument();
    // 仅台词带 speaker 输入框 → 只有一个 speaker 框
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
    // 第一条是画外音，其类型按钮文案为「画外音」
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
    expect(screen.getByText("本场景暂无发声内容。")).toBeInTheDocument();
  });
});
