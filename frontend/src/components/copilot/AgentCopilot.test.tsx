import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useAssistantSession } from "@/hooks/useAssistantSession";
import { useAppStore } from "@/stores/app-store";
import { useAssistantStore } from "@/stores/assistant-store";
import { useProjectsStore } from "@/stores/projects-store";
import { UI_LAYERS } from "@/utils/ui-layers";
import { AgentCopilot } from "./AgentCopilot";

vi.mock("@/hooks/useAssistantSession", () => ({
  useAssistantSession: vi.fn(),
}));

vi.mock("./ContextBanner", () => ({
  ContextBanner: () => <div data-testid="context-banner" />,
}));

vi.mock("./SlashCommandMenu", () => ({
  SlashCommandMenu: vi.fn(() => null),
}));

vi.mock("./chat/ChatMessage", () => ({
  ChatMessage: ({ message }: { message: { type: string } }) => (
    <div data-testid="chat-message">{message.type}</div>
  ),
}));

const mockedUseAssistantSession = vi.mocked(useAssistantSession);

function makePendingQuestion() {
  return {
    question_id: "q-1",
    questions: [
      {
        header: "输出",
        question: "输出格式是什么？",
        multiSelect: false,
        options: [
          { label: "摘要", description: "简洁输出" },
          { label: "详细", description: "完整说明" },
        ],
      },
    ],
  };
}

describe("AgentCopilot", () => {
  // Mocks whose callers wrap them with voidPromise must return a Promise
  // so the .catch(...) chain in voidPromise resolves instead of crashing.
  const sendMessage = vi.fn().mockResolvedValue(undefined);
  const answerQuestion = vi.fn().mockResolvedValue(undefined);
  const interrupt = vi.fn().mockResolvedValue(undefined);
  const createNewSession = vi.fn();
  const switchSession = vi.fn().mockResolvedValue(undefined);
  const deleteSession = vi.fn().mockResolvedValue(undefined);

  beforeEach(() => {
    useAssistantStore.setState(useAssistantStore.getInitialState(), true);
    useProjectsStore.setState(useProjectsStore.getInitialState(), true);
    useAppStore.setState(useAppStore.getInitialState(), true);
    vi.clearAllMocks();

    useProjectsStore.getState().setCurrentProject("demo", null);
    mockedUseAssistantSession.mockReturnValue({
      sendMessage,
      answerQuestion,
      interrupt,
      createNewSession,
      switchSession,
      deleteSession,
    });
  });

  it("renders the pending-question wizard and disables normal sending", () => {
    useAssistantStore.setState({
      pendingQuestion: makePendingQuestion(),
      skills: [{ name: "plan", description: "Plan", scope: "project", path: "/tmp/plan" }],
    });

    render(<AgentCopilot />);

    expect(screen.getByText("需要你的选择")).toBeInTheDocument();
    expect(screen.getByLabelText("助手输入")).toBeDisabled();
    expect(screen.getByLabelText("发送消息")).toBeDisabled();
    expect(screen.getByPlaceholderText("请先回答上方问题")).toBeInTheDocument();
  });

  it("submits wizard answers through answerQuestion", () => {
    useAssistantStore.setState({
      pendingQuestion: makePendingQuestion(),
    });

    render(<AgentCopilot />);

    fireEvent.click(screen.getByLabelText("摘要"));
    fireEvent.click(screen.getByRole("button", { name: /完成并提交/ }));

    expect(answerQuestion).toHaveBeenCalledWith("q-1", {
      "输出格式是什么？": "摘要",
    });
  });

  it("keeps assistant root isolated and uses local popover layer for session history", () => {
    useAssistantStore.setState({
      sessions: [
        {
          id: "session-1",
          project_name: "demo",
          title: "当前会话",
          status: "idle",
          created_at: "2026-02-01T00:00:00Z",
          updated_at: "2026-02-01T00:00:00Z",
        },
      ],
      currentSessionId: "session-1",
    });

    const { container } = render(<AgentCopilot />);

    expect(container.firstElementChild).toHaveClass("isolate");

    fireEvent.click(screen.getByTitle("切换会话"));
    expect(document.querySelector(`.${UI_LAYERS.assistantLocalPopover}`)).toBeTruthy();
  });

  it("does not send when Enter is used to confirm an IME composition", () => {
    render(<AgentCopilot />);

    const textarea = screen.getByLabelText("助手输入");
    fireEvent.change(textarea, { target: { value: "你好" } });

    fireEvent.compositionStart(textarea);
    fireEvent.keyDown(textarea, {
      key: "Enter",
      code: "Enter",
      keyCode: 229,
      which: 229,
      isComposing: true,
    });

    expect(sendMessage).not.toHaveBeenCalled();

    fireEvent.compositionEnd(textarea);
    fireEvent.keyDown(textarea, {
      key: "Enter",
      code: "Enter",
      keyCode: 13,
      which: 13,
    });

    expect(sendMessage).toHaveBeenCalledWith("你好", undefined);
  });
});
