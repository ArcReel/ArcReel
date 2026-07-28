import { useState } from "react";
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { UtteranceListEditor } from "./UtteranceListEditor";
import type { Utterance } from "@/types";

const sample: Utterance[] = [
  { kind: "voiceover", speaker: null, text: "三年后。" },
  { kind: "dialogue", speaker: "阿离", text: "你终于回来了。" },
];

/** 受控宿主：把 onChange 回写进 state，还原真实的删除 / 移动后重渲染。 */
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

  // 稳定 key 回归：数组索引作 key 时删除 / 移动会按位复用受控输入节点，
  // 焦点跳到错误行、编辑内容串位。以下断言焦点始终跟随原条目。
  it("keeps focus on a later row after an earlier row is removed", () => {
    const rows: Utterance[] = [
      { kind: "voiceover", speaker: null, text: "首行画外音。" },
      { kind: "dialogue", speaker: "阿离", text: "中间台词。" },
      { kind: "voiceover", speaker: null, text: "末行画外音。" },
    ];
    render(<Harness initial={rows} />);

    const lastRow = screen.getByDisplayValue("末行画外音。") as HTMLTextAreaElement;
    lastRow.focus();
    expect(document.activeElement).toBe(lastRow);

    fireEvent.click(screen.getAllByLabelText("删除发声")[0]);

    // 末行条目仍在、内容不变，且焦点仍停在同一节点（未被前移的节点顶替）。
    const stillLast = screen.getByDisplayValue("末行画外音。");
    expect(document.activeElement).toBe(stillLast);
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

    // 上移末行：其内容与焦点应随条目移动，不被相邻行内容顶替。
    fireEvent.click(screen.getAllByLabelText("上移")[2]);

    const focused = document.activeElement as HTMLTextAreaElement;
    expect(focused.value).toBe("末行画外音。");
  });
});
