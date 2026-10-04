import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";
import type { ContentBlock, Turn } from "@/types";
import { useAssistantStore } from "@/stores/assistant-store";
import { SubagentCard } from "./SubagentCard";
import { ThinkingBlock } from "./ThinkingBlock";
import { ContentBlockRenderer } from "./ContentBlockRenderer";

// ---------------------------------------------------------------------------
// 主时间线的单行条目：工序行（工具调用、Skill、子智能体、后台任务）与思考块
// ---------------------------------------------------------------------------

beforeEach(() => {
  useAssistantStore.getState().setSessionStatus("running");
});

function renderBlock(block: ContentBlock) {
  return render(<ContentBlockRenderer block={block} index={0} />);
}

describe("工具调用的工序行", () => {
  it("collapses by default and expands into parameter and result sections", () => {
    renderBlock({
      type: "tool_use",
      id: "tu-read",
      name: "Read",
      input: { file_path: "scripts/episode_1.json" },
      result: "{\"shots\": []}",
    });

    const row = screen.getByRole("button", { name: /读取文件/ });
    expect(row).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByText("参数")).not.toBeInTheDocument();

    fireEvent.click(row);
    expect(screen.getByText("参数")).toBeInTheDocument();
    expect(screen.getByText("结果")).toBeInTheDocument();
    expect(screen.getByText('{"shots": []}')).toBeInTheDocument();
  });

  it("marks a failed tool in its collapsed row and a running tool while the session runs", () => {
    renderBlock({ type: "tool_use", id: "a", name: "Bash", input: { command: "ls" }, result: "denied", is_error: true });
    renderBlock({ type: "tool_use", id: "b", name: "Grep", input: { pattern: "雨夜" } });

    expect(screen.getByRole("button", { name: /运行命令.*失败/ })).toHaveAttribute("aria-expanded", "false");
    expect(screen.getByRole("button", { name: /搜索内容.*运行中/ })).toBeInTheDocument();
  });

  it("shows a tool without result as stopped once the session is terminal", () => {
    useAssistantStore.getState().setSessionStatus("interrupted");
    renderBlock({ type: "tool_use", id: "a", name: "Grep", input: { pattern: "雨夜" } });
    expect(screen.getByRole("button", { name: /搜索内容.*已停止/ })).toBeInTheDocument();
  });

  it("shows the head of a long result and reveals the rest on demand", () => {
    const lines = Array.from({ length: 30 }, (_, i) => `第 ${i + 1} 行`);
    renderBlock({ type: "tool_use", id: "a", name: "Bash", input: { command: "cat" }, result: lines.join("\n") });

    fireEvent.click(screen.getByRole("button", { name: /运行命令/ }));
    expect(screen.getByText(/第 10 行/)).toBeInTheDocument();
    expect(screen.queryByText(/第 30 行/)).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "显示全部" }));
    expect(screen.getByText(/第 30 行/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "收起" }));
    expect(screen.queryByText(/第 30 行/)).not.toBeInTheDocument();
  });
});

describe("Skill 的工序行", () => {
  it("expands a successful skill to reveal its recorded arguments and result", () => {
    renderBlock({
      type: "tool_use",
      id: "tu-skill",
      name: "Skill",
      input: { skill: "generate-storyboard", args: "第一集所有场景" },
      result: "Launching skill: generate-storyboard",
    });

    const row = screen.getByRole("button", { name: /\/generate-storyboard.*第一集所有场景/ });
    expect(row).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByText("参数")).not.toBeInTheDocument();
    expect(screen.queryByText("Launching skill: generate-storyboard")).not.toBeInTheDocument();

    fireEvent.click(row);
    expect(screen.getByText("参数")).toBeInTheDocument();
    expect(screen.getByText("结果")).toBeInTheDocument();
    expect(screen.getByText("Launching skill: generate-storyboard")).toBeInTheDocument();
  });

  it("keeps a skill without recorded arguments or result non-expandable", () => {
    renderBlock({ type: "skill_invocation", skill_name: "generate-storyboard" });

    expect(screen.getByText("/generate-storyboard")).toBeInTheDocument();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("expands the arguments of a standalone skill invocation", () => {
    renderBlock({ type: "skill_invocation", skill_name: "generate-storyboard", skill_args: "第一集所有场景" });

    fireEvent.click(screen.getByRole("button", { name: /\/generate-storyboard.*第一集所有场景/ }));
    expect(screen.getByText("参数")).toBeInTheDocument();
    expect(screen.queryByText("结果")).not.toBeInTheDocument();
  });

  it("dispatches Skill tool_use blocks to the skill row and exposes the error when it fails", () => {
    renderBlock({
      type: "tool_use",
      id: "tu-1",
      name: "Skill",
      input: { skill: "commit", args: "" },
      result: "Unknown skill: commit",
      is_error: true,
    });

    fireEvent.click(screen.getByRole("button", { name: /\/commit.*失败/ }));
    expect(screen.getByText("Unknown skill: commit")).toBeInTheDocument();
  });
});

describe("后台任务的工序行", () => {
  it("reports a task failure when collapsed and expands its description and outcome", () => {
    renderBlock({
      type: "task_progress",
      task_id: "t1",
      status: "task_notification",
      task_status: "failed",
      description: "批量生成分镜图",
      summary: "供应商拒绝了请求",
    });
    expect(screen.getByText("供应商拒绝了请求")).toBeInTheDocument();
    expect(screen.getByText("失败")).toBeInTheDocument();
    const row = screen.getByRole("button", { name: /后台任务.*供应商拒绝了请求.*失败/ });
    expect(row).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByText("批量生成分镜图")).not.toBeInTheDocument();

    fireEvent.click(row);
    expect(screen.getByText("参数")).toBeInTheDocument();
    expect(screen.getByText("批量生成分镜图")).toBeInTheDocument();
    expect(screen.getByText("结果")).toBeInTheDocument();
  });
});

describe("ThinkingBlock", () => {
  it("shows a single-line thinking indicator while streaming", () => {
    render(<ThinkingBlock thinking="部分推理" streaming />);

    expect(screen.getByText("正在思考")).toBeInTheDocument();
    expect(screen.queryByText("部分推理")).not.toBeInTheDocument();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("collapses to one labelled line and expands the full text on click", () => {
    const thinking = "先分析项目状态\n再决定生成顺序";
    render(<ThinkingBlock thinking={thinking} />);

    const toggle = screen.getByRole("button", { name: "思考过程" });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByText(/先分析项目状态/)).not.toBeInTheDocument();

    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText(/再决定生成顺序/)).toBeInTheDocument();
  });

  it("renders nothing for empty completed thinking", () => {
    const { container } = render(<ThinkingBlock thinking="  " />);
    expect(container).toBeEmptyDOMElement();
  });
});

describe("子智能体的工序行", () => {
  function makeBlock(overrides: Partial<ContentBlock> = {}): ContentBlock {
    const subTurns: Turn[] = [
      { type: "user", content: [{ type: "text", text: "内部 prompt" }], uuid: "s-u1" },
      {
        type: "assistant",
        content: [
          { type: "tool_use", id: "sub-read", name: "Read", input: { file_path: "lib/cost.py" }, result: "ok" },
          { type: "text", text: "子智能体回复" },
        ],
        uuid: "s-a1",
      },
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

  it("collapses by default showing the description and token usage while running", () => {
    render(
      <SubagentCard
        block={makeBlock({
          task_info: { type: "task_progress", status: "task_progress", usage: { total_tokens: 4200 } },
        })}
      />,
    );

    const row = screen.getByRole("button", { name: /子智能体.*探索费用计算逻辑.*4200 tokens.*运行中/ });
    expect(row).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByText("子智能体回复")).not.toBeInTheDocument();
  });

  it("expands to the sub-timeline with its own work rows, followed by the conclusion", () => {
    renderBlock(makeBlock({ result: "费用按分镜数累加" }));

    fireEvent.click(screen.getByRole("button", { name: /探索费用计算逻辑/ }));

    expect(screen.getByText("内部 prompt")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /读取文件.*lib\/cost\.py/ })).toBeInTheDocument();
    expect(screen.getByText("子智能体回复")).toBeInTheDocument();
    expect(screen.getByText("子智能体结论")).toBeInTheDocument();
    expect(screen.getByText("费用按分镜数累加")).toBeInTheDocument();
  });

  it("shows stopped when the session ended before the subagent did", () => {
    useAssistantStore.getState().setSessionStatus("interrupted");
    render(<SubagentCard block={makeBlock()} />);
    expect(screen.getByRole("button", { name: /探索费用计算逻辑.*已停止/ })).toBeInTheDocument();
  });

  it("reads a synthesized card's outcome, description and conclusion from task_info", () => {
    render(
      <SubagentCard
        block={{
          type: "tool_use",
          id: "orphan",
          name: "Agent",
          sub_turns: [],
          task_info: {
            type: "task_progress",
            status: "task_notification",
            task_status: "stopped",
            description: "核对第 2 集的分镜",
            summary: "停在读取剧本之后",
          },
        }}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /核对第 2 集的分镜.*已停止/ }));
    expect(screen.getByText("停在读取剧本之后")).toBeInTheDocument();
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
        block={makeBlock({
          result: "failed",
          sub_turns: [{ type: "system", content: [{ type: "agent_failure", failure }] }],
        })}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /探索费用计算逻辑/ }));

    expect(screen.getByRole("alert")).toHaveTextContent("Agent 运行时出错");
  });
});
