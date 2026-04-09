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
        header: "Output",
        question: "What is the output format?",
        multiSelect: false,
        options: [
          { label: "Summary", description: "Concise output" },
          { label: "Detailed", description: "Full explanation" },
        ],
      },
    ],
  };
}

describe("AgentCopilot", () => {
  const sendMessage = vi.fn();
  const answerQuestion = vi.fn();
  const interrupt = vi.fn();
  const createNewSession = vi.fn();
  const switchSession = vi.fn();
  const deleteSession = vi.fn();

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

    expect(screen.getByText("Your input needed")).toBeInTheDocument();
    expect(screen.getByLabelText("Assistant input")).toBeDisabled();
    expect(screen.getByLabelText("Send message")).toBeDisabled();
    expect(screen.getByPlaceholderText("Please answer the question above first")).toBeInTheDocument();
  });

  it("submits wizard answers through answerQuestion", () => {
    useAssistantStore.setState({
      pendingQuestion: makePendingQuestion(),
    });

    render(<AgentCopilot />);

    fireEvent.click(screen.getByLabelText("Summary"));
    fireEvent.click(screen.getByRole("button", { name: "Submit answers" }));

    expect(answerQuestion).toHaveBeenCalledWith("q-1", {
      "What is the output format?": "Summary",
    });
  });

  it("keeps assistant root isolated and uses local popover layer for session history", () => {
    useAssistantStore.setState({
      sessions: [
        {
          id: "session-1",
          project_name: "demo",
          title: "Current Session",
          status: "idle",
          created_at: "2026-02-01T00:00:00Z",
          updated_at: "2026-02-01T00:00:00Z",
        },
      ],
      currentSessionId: "session-1",
    });

    const { container } = render(<AgentCopilot />);

    expect(container.firstElementChild).toHaveClass("isolate");

    fireEvent.click(screen.getByTitle("Switch session"));
    expect(document.querySelector(`.${UI_LAYERS.assistantLocalPopover}`)).toBeTruthy();
  });
});
