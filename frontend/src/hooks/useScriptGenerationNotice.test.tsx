import { act, render } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useScriptGenerationNotice } from "@/hooks/useScriptGenerationNotice";
import { useAssistantStore } from "@/stores/assistant-store";
import { useAppStore } from "@/stores/app-store";
import type { ContentBlock, Turn } from "@/types";

function Harness() {
  useScriptGenerationNotice();
  return null;
}

function toolUse(name: string, id: string): ContentBlock {
  return { type: "tool_use", name, id, input: {} };
}

// 后端 turn_grouper 把 tool_result 合并进发起调用的 tool_use 块的 result/is_error
// 字段（同一 turn 内配对），完成的调用没有独立 tool_result 块。这是 UI 真实收到的形状。
function completedToolUse(name: string, id: string): ContentBlock {
  return { type: "tool_use", name, id, input: {}, result: "✅ done", is_error: false };
}

function toolResult(toolUseId: string): ContentBlock {
  return { type: "tool_result", tool_use_id: toolUseId, content: "✅ done" };
}

function assistantTurn(...content: ContentBlock[]): Turn {
  return { type: "assistant", content };
}

const SCRIPT_TOOL = "mcp__arcreel__generate_episode_script";
const NORMALIZE_TOOL = "mcp__arcreel__normalize_drama_script";

describe("useScriptGenerationNotice", () => {
  beforeEach(() => {
    useAppStore.setState(useAppStore.getInitialState(), true);
    useAssistantStore.setState(useAssistantStore.getInitialState(), true);
  });

  it("pushes one info toast when a running session starts a script generation tool", () => {
    useAssistantStore.setState({ sessionStatus: "running" });
    const spy = vi.spyOn(useAppStore.getState(), "pushToast");
    render(<Harness />);

    act(() => {
      useAssistantStore.setState({
        draftTurn: assistantTurn(toolUse(SCRIPT_TOOL, "tu-1")),
      });
    });

    expect(spy).toHaveBeenCalledTimes(1);
    expect(spy.mock.calls[0][1]).toBe("info");
  });

  it("also fires for the drama-script normalization tool", () => {
    useAssistantStore.setState({ sessionStatus: "running" });
    const spy = vi.spyOn(useAppStore.getState(), "pushToast");
    render(<Harness />);

    act(() => {
      useAssistantStore.setState({
        draftTurn: assistantTurn(toolUse(NORMALIZE_TOOL, "tu-n")),
      });
    });

    expect(spy).toHaveBeenCalledTimes(1);
  });

  it("does not fire for non-script tool calls", () => {
    useAssistantStore.setState({ sessionStatus: "running" });
    const spy = vi.spyOn(useAppStore.getState(), "pushToast");
    render(<Harness />);

    act(() => {
      useAssistantStore.setState({
        draftTurn: assistantTurn(
          toolUse("mcp__arcreel__generate_storyboards", "tu-sb"),
          toolUse("Bash", "tu-bash"),
        ),
      });
    });

    expect(spy).not.toHaveBeenCalled();
  });

  it("does not fire twice for the same tool_use id across re-renders/reconnect", () => {
    useAssistantStore.setState({ sessionStatus: "running" });
    const spy = vi.spyOn(useAppStore.getState(), "pushToast");
    render(<Harness />);

    act(() => {
      useAssistantStore.setState({
        draftTurn: assistantTurn(toolUse(SCRIPT_TOOL, "tu-1")),
      });
    });
    // Simulate an SSE reconnect re-delivering the same in-flight draft turn.
    act(() => {
      useAssistantStore.setState({ draftTurn: null });
      useAssistantStore.setState({
        draftTurn: assistantTurn(toolUse(SCRIPT_TOOL, "tu-1")),
      });
    });

    expect(spy).toHaveBeenCalledTimes(1);
  });

  it("does not fire when the tool call already has a result (historical/completed)", () => {
    useAssistantStore.setState({ sessionStatus: "running" });
    const spy = vi.spyOn(useAppStore.getState(), "pushToast");
    render(<Harness />);

    act(() => {
      useAssistantStore.setState({
        turns: [
          assistantTurn(toolUse(SCRIPT_TOOL, "tu-done")),
          { type: "user", content: [toolResult("tu-done")] },
        ],
      });
    });

    expect(spy).not.toHaveBeenCalled();
  });

  it("does not fire when a running session reloads a completed script call (result merged onto tool_use)", () => {
    // 会话停在 AskUserQuestion 时后端 status 仍为 "running"。重新进入项目会重载快照，
    // 其中已完成的剧本工具调用以「tool_use 带 result」形式回放（无独立 tool_result 块）。
    // 该回放不应再弹「耗时」提示。
    useAssistantStore.setState({ sessionStatus: "running" });
    const spy = vi.spyOn(useAppStore.getState(), "pushToast");
    render(<Harness />);

    act(() => {
      useAssistantStore.setState({
        turns: [
          assistantTurn(
            completedToolUse(SCRIPT_TOOL, "tu-done"),
            toolUse("AskUserQuestion", "tu-q"),
          ),
        ],
      });
    });

    expect(spy).not.toHaveBeenCalled();
  });

  it("treats a failed-but-completed call (only is_error, no result) as completed", () => {
    // 注释与 ContentBlock 契约把 result/is_error 都列为完成信号。即便回放一个仅带
    // is_error 而无 result 的失败完成块，也不应被误判为进行中而弹「耗时」提示。
    useAssistantStore.setState({ sessionStatus: "running" });
    const spy = vi.spyOn(useAppStore.getState(), "pushToast");
    render(<Harness />);

    act(() => {
      useAssistantStore.setState({
        draftTurn: assistantTurn({
          type: "tool_use",
          name: SCRIPT_TOOL,
          id: "tu-err",
          input: {},
          is_error: true,
        }),
      });
    });

    expect(spy).not.toHaveBeenCalled();
  });

  it("does not fire when the session is not running (idle/interrupted reload)", () => {
    useAssistantStore.setState({ sessionStatus: "interrupted" });
    const spy = vi.spyOn(useAppStore.getState(), "pushToast");
    render(<Harness />);

    act(() => {
      useAssistantStore.setState({
        draftTurn: assistantTurn(toolUse(SCRIPT_TOOL, "tu-int")),
      });
    });

    expect(spy).not.toHaveBeenCalled();
  });

  it("does not re-fire after switching away and back to the same running call", () => {
    useAssistantStore.setState({ sessionStatus: "running" });
    const spy = vi.spyOn(useAppStore.getState(), "pushToast");
    render(<Harness />);

    act(() => {
      useAssistantStore.setState({
        draftTurn: assistantTurn(toolUse(SCRIPT_TOOL, "tu-keep")),
      });
    });
    // Switch away: turns/draft reset, status drops to idle.
    act(() => {
      useAssistantStore.setState({ draftTurn: null, turns: [], sessionStatus: "idle" });
    });
    // Switch back: snapshot reloads the still-running call (same id).
    act(() => {
      useAssistantStore.setState({
        sessionStatus: "running",
        draftTurn: assistantTurn(toolUse(SCRIPT_TOOL, "tu-keep")),
      });
    });

    expect(spy).toHaveBeenCalledTimes(1);
  });
});
