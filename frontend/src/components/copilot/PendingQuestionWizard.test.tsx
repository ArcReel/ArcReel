import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { PendingQuestion } from "@/types";
import { PendingQuestionWizard } from "./PendingQuestionWizard";

function makePendingQuestion(overrides: Partial<PendingQuestion> = {}): PendingQuestion {
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
      {
        header: "Sections",
        question: "Which sections to include?",
        multiSelect: true,
        options: [
          { label: "Introduction", description: "Opening context" },
          { label: "Conclusion", description: "Closing summary" },
        ],
      },
    ],
    ...overrides,
  };
}

describe("PendingQuestionWizard", () => {
  it("renders only the current question and blocks next until answered", () => {
    render(
      <PendingQuestionWizard
        pendingQuestion={makePendingQuestion()}
        answeringQuestion={false}
        error={null}
        onSubmitAnswers={vi.fn()}
      />,
    );

    expect(screen.getByText("Question 1/2")).toBeInTheDocument();
    expect(screen.getByText("What is the output format?")).toBeInTheDocument();
    expect(screen.queryByText("Which sections to include?")).not.toBeInTheDocument();

    const nextButton = screen.getByRole("button", { name: "Next" });
    expect(nextButton).toBeDisabled();

    fireEvent.click(screen.getByLabelText("Summary"));
    expect(nextButton).toBeEnabled();

    fireEvent.click(nextButton);
    expect(screen.getByText("Question 2/2")).toBeInTheDocument();
    expect(screen.getByText("Which sections to include?")).toBeInTheDocument();
    expect(screen.queryByText("What is the output format?")).not.toBeInTheDocument();
  });

  it("keeps answers when navigating backward", () => {
    render(
      <PendingQuestionWizard
        pendingQuestion={makePendingQuestion()}
        answeringQuestion={false}
        error={null}
        onSubmitAnswers={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByLabelText("Detailed"));
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    fireEvent.click(screen.getByRole("button", { name: "Previous" }));

    expect(screen.getByText("What is the output format?")).toBeInTheDocument();
    expect(screen.getByLabelText("Detailed")).toBeChecked();
  });

  it("validates custom other answers and joins multi-select payloads", () => {
    const onSubmitAnswers = vi.fn();

    render(
      <PendingQuestionWizard
        pendingQuestion={makePendingQuestion({
          questions: [
            {
              header: "Sections",
              question: "Which sections to include?",
              multiSelect: true,
              options: [
                { label: "Introduction", description: "Opening context" },
                { label: "Conclusion", description: "Closing summary" },
              ],
            },
          ],
        })}
        answeringQuestion={false}
        error={null}
        onSubmitAnswers={onSubmitAnswers}
      />,
    );

    fireEvent.click(screen.getByLabelText("Introduction"));
    fireEvent.click(screen.getByLabelText("Other"));

    const submitButton = screen.getByRole("button", { name: "Submit answers" });
    expect(submitButton).toBeDisabled();

    fireEvent.change(screen.getByPlaceholderText("Please enter custom value"), {
      target: { value: "Appendix" },
    });
    expect(submitButton).toBeEnabled();

    fireEvent.click(submitButton);

    expect(onSubmitAnswers).toHaveBeenCalledWith("q-1", {
      "Which sections to include?": "Introduction, Appendix",
    });
  });

  it("resets local wizard state when question_id changes", () => {
    const { rerender } = render(
      <PendingQuestionWizard
        pendingQuestion={makePendingQuestion()}
        answeringQuestion={false}
        error={null}
        onSubmitAnswers={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByLabelText("Summary"));
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    expect(screen.getByText("Which sections to include?")).toBeInTheDocument();

    rerender(
      <PendingQuestionWizard
        pendingQuestion={makePendingQuestion({ question_id: "q-2" })}
        answeringQuestion={false}
        error={null}
        onSubmitAnswers={vi.fn()}
      />,
    );

    expect(screen.getByText("What is the output format?")).toBeInTheDocument();
    expect(screen.queryByText("Which sections to include?")).not.toBeInTheDocument();
    expect(screen.getByLabelText("Summary")).not.toBeChecked();
    expect(screen.getByRole("button", { name: "Next" })).toBeDisabled();
  });

  it("keeps the action area visible by making question content scrollable", () => {
    render(
      <PendingQuestionWizard
        pendingQuestion={makePendingQuestion({
          questions: [
            {
              header: "Very long question",
              question: "This is a very long question. ".repeat(120),
              multiSelect: false,
              options: [
                { label: "Continue", description: "Continue processing" },
              ],
            },
          ],
        })}
        answeringQuestion={false}
        error={null}
        onSubmitAnswers={vi.fn()}
      />,
    );

    expect(screen.getByTestId("pending-question-scroll-area")).toHaveClass("overflow-y-auto");
    expect(screen.getByRole("button", { name: "Submit answers" })).toBeInTheDocument();
  });
});
