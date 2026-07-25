import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";
import type { ContentBlock, Turn } from "@/types";
import { useAssistantStore } from "@/stores/assistant-store";
import { SkillChip } from "./SkillChip";
import { SubagentCard } from "./SubagentCard";
import { ThinkingBlock } from "./ThinkingBlock";
import { ContentBlockRenderer } from "./ContentBlockRenderer";

// ---------------------------------------------------------------------------
// 主时间线信息密度三元素：skill 芯片 / thinking 单行条 / subagent 折叠卡片
// ---------------------------------------------------------------------------

describe("SkillChip", () => {
  it("renders /skill-name with args and no injected content", () => {
    render(<SkillChip name="generate-storyboard" args="第一集所有场景" />);

    expect(screen.getByText("/generate-storyboard")).toBeInTheDocument();
    expect(screen.getByText("第一集所有场景")).toBeInTheDocument();
    // 芯片不可展开：无按钮交互
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("dispatches Skill tool_use blocks to the chip instead of the tool card", () => {
    const block: ContentBlock = {
      type: "tool_use",
      id: "tu-1",
      name: "Skill",
      input: { skill: "commit", args: "" },
      result: "Launching skill: commit",
    };
    render(<ContentBlockRenderer block={block} index={0} />);

    expect(screen.getByText("/commit")).toBeInTheDocument();
    // 不再出现工具卡的输入参数展开区
    expect(screen.queryByText("输入参数")).not.toBeInTheDocument();
  });

  it("renders standalone skill_invocation blocks as a chip", () => {
    const block: ContentBlock = { type: "skill_invocation", skill_name: "manage-project" };
    render(<ContentBlockRenderer block={block} index={0} />);

    expect(screen.getByText("/manage-project")).toBeInTheDocument();
  });
});

describe("ThinkingBlock", () => {
  it("shows an animated single-line thinking indicator while streaming", () => {
    render(<ThinkingBlock thinking="部分推理" streaming />);

    expect(screen.getByText("思考中…")).toBeInTheDocument();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("collapses to a one-line summary and expands full text on click", () => {
    const thinking = "先分析项目状态\n再决定生成顺序";
    render(<ThinkingBlock thinking={thinking} />);

    const toggle = screen.getByRole("button");
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(screen.getByText("先分析项目状态")).toBeInTheDocument();
    expect(screen.queryByText(/再决定生成顺序/)).not.toBeInTheDocument();

    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText(/再决定生成顺序/)).toBeInTheDocument();
  });

  it("renders nothing for empty completed thinking", () => {
    const { container } = render(<ThinkingBlock thinking="  " />);
    expect(container).toBeEmptyDOMElement();
  });
});

describe("SubagentCard", () => {
  beforeEach(() => {
    useAssistantStore.getState().setSessionStatus("running");
  });

  function makeCardBlock(overrides: Partial<ContentBlock> = {}): ContentBlock {
    const subTurns: Turn[] = [
      { type: "user", content: [{ type: "text", text: "内部 prompt" }], uuid: "s-u1" },
      { type: "assistant", content: [{ type: "text", text: "子任务回复" }], uuid: "s-a1" },
    ];
    return {
      type: "tool_use",
      id: "tu-agent",
      name: "Agent",
      input: { subagent_type: "Explore", description: "探索费用计算逻辑" },
      sub_turns: subTurns,
      ...overrides,
    };
  }

  it("collapses by default showing description, status and progress", () => {
    render(
      <SubagentCard
        block={makeCardBlock({
          task_info: { type: "task_progress", status: "task_progress", usage: { total_tokens: 4200 } },
        })}
      />,
    );

    expect(screen.getByText("探索费用计算逻辑")).toBeInTheDocument();
    expect(screen.getByText("运行中")).toBeInTheDocument();
    expect(screen.getByText("4200 tokens")).toBeInTheDocument();
    // 默认收起：子时间线不可见
    expect(screen.queryByText("子任务回复")).not.toBeInTheDocument();
  });

  it("expands to reveal the sub-timeline", () => {
    render(<SubagentCard block={makeCardBlock({ result: "报告全文" })} />);

    const toggle = screen.getByRole("button");
    fireEvent.click(toggle);

    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText("内部 prompt")).toBeInTheDocument();
    expect(screen.getByText("子任务回复")).toBeInTheDocument();
  });

  it("derives completed status from the tool result", () => {
    render(<SubagentCard block={makeCardBlock({ result: "done" })} />);
    expect(screen.getByText("已完成")).toBeInTheDocument();
  });

  it("shows stopped instead of a spinner when the session is terminal", () => {
    useAssistantStore.getState().setSessionStatus("interrupted");
    render(<SubagentCard block={makeCardBlock()} />);
    expect(screen.getByText("已停止")).toBeInTheDocument();
  });

  it("dispatches Agent tool_use blocks to the card", () => {
    render(<ContentBlockRenderer block={makeCardBlock()} index={0} />);
    expect(screen.getByText("探索费用计算逻辑")).toBeInTheDocument();
  });

  it("renders a failure card in the expanded sub-timeline", () => {
    const failure = {
      version: 1,
      phase: "turn" as const,
      timestamp: "2026-07-23T00:00:00Z",
      project_name: "demo",
      session_id: "session-1",
      summary: {
        source: "sdk_result",
        type: "error_during_execution",
        message: "subagent failed",
      },
      raw: { result_message: { type: "result", is_error: true } },
    };
    render(
      <SubagentCard
        block={makeCardBlock({
          result: "failed",
          sub_turns: [{ type: "system", content: [{ type: "agent_failure", failure }] }],
        })}
      />,
    );

    fireEvent.click(screen.getByRole("button"));

    expect(screen.getByRole("alert")).toBeInTheDocument();
    expect(screen.getByText("subagent failed")).toBeInTheDocument();
  });
});
