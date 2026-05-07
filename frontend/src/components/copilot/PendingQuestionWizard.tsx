import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Sparkles } from "lucide-react";

const OTHER_TEXTAREA_MAX_PX = 160;
import type { PendingQuestion } from "@/types";
import {
  buildAnswersPayload,
  buildQuestionOptions,
  getNextVisitedSteps,
  getQuestionKey,
  isOtherSelected,
  isQuestionAnswerReady,
} from "./pending-question";

interface PendingQuestionWizardProps {
  pendingQuestion: PendingQuestion;
  answeringQuestion: boolean;
  error: string | null;
  onSubmitAnswers: (questionId: string, answers: Record<string, string>) => void;
}

export function PendingQuestionWizard({
  pendingQuestion,
  answeringQuestion,
  error,
  onSubmitAnswers,
}: PendingQuestionWizardProps) {
  const { t } = useTranslation("dashboard");
  const pendingQuestions = pendingQuestion.questions;
  const [questionAnswers, setQuestionAnswers] = useState<Record<string, string | string[]>>({});
  const [questionCustomAnswers, setQuestionCustomAnswers] = useState<Record<string, string>>({});
  const [currentQuestionIndex, setCurrentQuestionIndex] = useState(0);
  const [visitedQuestionIndexes, setVisitedQuestionIndexes] = useState<number[]>([]);
  const otherTextareaRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    const initialAnswers: Record<string, string | string[]> = {};
    const initialCustomAnswers: Record<string, string> = {};

    pendingQuestions.forEach((question, index) => {
      const key = getQuestionKey(question, index);
      initialAnswers[key] = question.multiSelect ? [] : "";
      initialCustomAnswers[key] = "";
    });

    // eslint-disable-next-line react-hooks/set-state-in-effect -- 新问题到来时重置向导所有状态，是有意的受控重置模式
    setQuestionAnswers(initialAnswers);
    setQuestionCustomAnswers(initialCustomAnswers);
    setCurrentQuestionIndex(0);
    setVisitedQuestionIndexes(pendingQuestions.length > 0 ? [0] : []);
  }, [pendingQuestion.question_id, pendingQuestion.questions, pendingQuestions]);

  const totalQuestions = pendingQuestions.length;
  const normalizedQuestionIndex = totalQuestions === 0
    ? 0
    : Math.min(currentQuestionIndex, totalQuestions - 1);
  const currentQuestion = totalQuestions > 0 ? pendingQuestions[normalizedQuestionIndex] : null;
  const currentQuestionKey = currentQuestion ? getQuestionKey(currentQuestion, normalizedQuestionIndex) : "";
  const currentQuestionAnswer = currentQuestionKey ? questionAnswers[currentQuestionKey] ?? "" : "";
  const currentQuestionCustomAnswer = currentQuestionKey ? questionCustomAnswers[currentQuestionKey] ?? "" : "";
  const currentQuestionOptions = currentQuestion ? buildQuestionOptions(currentQuestion.options) : [];
  const isFirstQuestion = normalizedQuestionIndex <= 0;
  const isLastQuestion = totalQuestions > 0 && normalizedQuestionIndex === totalQuestions - 1;

  const currentQuestionReady = useMemo(() => {
    if (!currentQuestion) {
      return false;
    }
    return isQuestionAnswerReady(
      currentQuestion,
      currentQuestionAnswer,
      currentQuestionCustomAnswer,
    );
  }, [currentQuestion, currentQuestionAnswer, currentQuestionCustomAnswer]);

  const allQuestionsReady = useMemo(() => {
    if (pendingQuestions.length === 0) {
      return false;
    }

    return pendingQuestions.every((question, index) => {
      const key = getQuestionKey(question, index);
      return isQuestionAnswerReady(
        question,
        questionAnswers[key] ?? (question.multiSelect ? [] : ""),
        questionCustomAnswers[key] ?? "",
      );
    });
  }, [pendingQuestions, questionAnswers, questionCustomAnswers]);

  // Re-measure the "other" textarea height whenever the visible question or its
  // backing draft changes. Lets the textarea grow upward as the user types and
  // restores correct height when navigating back to a step with an existing draft.
  useEffect(() => {
    const el = otherTextareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    const next = Math.min(el.scrollHeight, OTHER_TEXTAREA_MAX_PX);
    el.style.height = `${next}px`;
    el.style.overflowY =
      el.scrollHeight > OTHER_TEXTAREA_MAX_PX ? "auto" : "hidden";
  }, [currentQuestionAnswer, currentQuestionCustomAnswer, currentQuestionKey]);

  if (pendingQuestions.length === 0) {
    return null;
  }

  function setSingleQuestionAnswer(questionKey: string, value: string): void {
    setQuestionAnswers((previous) => ({
      ...previous,
      [questionKey]: value,
    }));
  }

  function toggleMultiQuestionAnswer(questionKey: string, value: string, checked: boolean): void {
    setQuestionAnswers((previous) => {
      const current = Array.isArray(previous[questionKey]) ? previous[questionKey] : [];
      const next = checked
        ? Array.from(new Set([...current, value]))
        : current.filter((item) => item !== value);
      return {
        ...previous,
        [questionKey]: next,
      };
    });
  }

  function setCustomQuestionAnswer(questionKey: string, value: string): void {
    setQuestionCustomAnswers((previous) => ({
      ...previous,
      [questionKey]: value,
    }));
  }

  function handlePreviousQuestion(): void {
    if (answeringQuestion) return;
    setCurrentQuestionIndex((previous) => Math.max(0, previous - 1));
  }

  function handleNextQuestion(): void {
    if (answeringQuestion || !currentQuestionReady) return;

    setCurrentQuestionIndex((previous) => {
      const next = Math.min(totalQuestions - 1, previous + 1);
      setVisitedQuestionIndexes((visited) => getNextVisitedSteps(visited, next));
      return next;
    });
  }

  function handleSelectQuestionStep(index: number): void {
    if (answeringQuestion) return;
    if (index < 0 || index >= totalQuestions) return;
    if (!visitedQuestionIndexes.includes(index) && index !== normalizedQuestionIndex) return;
    setCurrentQuestionIndex(index);
  }

  function handleSubmit(event: React.FormEvent<HTMLFormElement>): void {
    event.preventDefault();
    if (answeringQuestion || !allQuestionsReady) return;

    onSubmitAnswers(
      pendingQuestion.question_id,
      buildAnswersPayload(pendingQuestions, questionAnswers, questionCustomAnswers),
    );
  }

  return (
    <form
      className="relative px-3 py-3"
      style={{
        borderTop: "1px solid var(--color-accent-soft)",
        background:
          "linear-gradient(180deg, oklch(0.76 0.09 295 / 0.10), transparent 60%), oklch(0.18 0.010 265 / 0.6)",
        backdropFilter: "blur(10px)",
        WebkitBackdropFilter: "blur(10px)",
      }}
      onSubmit={handleSubmit}
    >
      {/* Top-edge accent bar — softly glows to mark this region as "system asks" */}
      <div
        aria-hidden
        className="pointer-events-none absolute left-0 right-0 top-0 h-px"
        style={{
          background:
            "linear-gradient(90deg, transparent, var(--color-accent), transparent)",
          opacity: 0.6,
        }}
      />

      <div className="flex max-h-[min(30rem,50vh)] min-h-0 flex-col gap-2.5">
        {/* Header line: sparkle + label + step counter */}
        <div className="flex shrink-0 items-center justify-between gap-2">
          <div className="flex items-center gap-1.5">
            <Sparkles
              className="h-3 w-3"
              style={{ color: "var(--color-accent)" }}
            />
            <span
              className="text-[10px] font-semibold uppercase"
              style={{
                color: "var(--color-accent-2)",
                letterSpacing: "0.18em",
              }}
            >
              {t("pending_question_wizard_label")}
            </span>
          </div>
          <span
            className="num text-[10px]"
            style={{ color: "var(--color-text-4)", letterSpacing: "0.06em" }}
          >
            {`${String(normalizedQuestionIndex + 1).padStart(2, "0")} / ${String(totalQuestions).padStart(2, "0")}`}
          </span>
        </div>

        {/* Step progress — thin segmented bar replaces verbose pills */}
        {totalQuestions > 1 && (
          <div className="flex shrink-0 items-center gap-1">
            {pendingQuestions.map((question, questionIndex) => {
              const isActiveStep = questionIndex === normalizedQuestionIndex;
              const isVisitedStep = isActiveStep || visitedQuestionIndexes.includes(questionIndex);
              return (
                <button
                  key={`${pendingQuestion.question_id}-step-${questionIndex}`}
                  type="button"
                  onClick={() => handleSelectQuestionStep(questionIndex)}
                  disabled={answeringQuestion || !isVisitedStep}
                  title={`${questionIndex + 1}. ${question.header || t("pending_question_wizard_step_question", { number: questionIndex + 1 })}`}
                  aria-label={`${questionIndex + 1}. ${question.header || t("pending_question_wizard_step_question", { number: questionIndex + 1 })}`}
                  className="h-[3px] flex-1 rounded-full transition-all disabled:cursor-not-allowed"
                  style={{
                    background: isActiveStep
                      ? "var(--color-accent)"
                      : isVisitedStep
                        ? "var(--color-accent-soft)"
                        : "oklch(0.30 0.012 265 / 0.4)",
                    boxShadow: isActiveStep
                      ? "0 0 8px var(--color-accent-glow)"
                      : "none",
                  }}
                />
              );
            })}
          </div>
        )}

        {currentQuestion && (
          <section
            className="min-h-0 flex-1 overflow-y-auto pr-1"
            data-testid="pending-question-scroll-area"
          >
            {/* Question hero — accent left bar + display-serif text */}
            <div
              className="relative pl-3 pr-1"
              style={{
                borderLeft: "2px solid var(--color-accent)",
              }}
            >
              <div className="mb-1 flex items-center gap-2">
                {currentQuestion.header && (
                  <span
                    className="text-[10.5px] font-semibold uppercase"
                    style={{
                      color: "var(--color-accent-2)",
                      letterSpacing: "0.12em",
                    }}
                  >
                    {currentQuestion.header}
                  </span>
                )}
                <span
                  className="text-[10px]"
                  style={{
                    color: "var(--color-text-4)",
                    letterSpacing: "0.06em",
                  }}
                >
                  {currentQuestion.header ? "· " : ""}
                  {currentQuestion.multiSelect
                    ? t("pending_question_wizard_multi_select")
                    : t("pending_question_wizard_single_select")}
                </span>
              </div>
              <p
                className="display-serif text-[14px] font-semibold leading-[1.45]"
                style={{ color: "var(--color-text)" }}
              >
                {currentQuestion.question || t("pending_question_wizard_select_option")}
              </p>
            </div>

            {/* Options — quiet rows separated by hairlines, not stacked boxes */}
            <div
              className="mt-3 overflow-hidden rounded-lg"
              style={{
                border: "1px solid var(--color-hairline-soft)",
                background: "oklch(0.16 0.010 265 / 0.45)",
              }}
            >
              {currentQuestionOptions.map((option, optionIndex) => {
                const checked = currentQuestion.multiSelect
                  ? Array.isArray(currentQuestionAnswer) && currentQuestionAnswer.includes(option.value)
                  : currentQuestionAnswer === option.value;
                const isLast = optionIndex === currentQuestionOptions.length - 1;

                return (
                  <label
                    key={`${currentQuestionKey}-${optionIndex}`}
                    className="relative block cursor-pointer transition-colors"
                    style={{
                      borderBottom: isLast
                        ? "none"
                        : "1px solid var(--color-hairline-soft)",
                      background: checked ? "var(--color-accent-dim)" : "transparent",
                    }}
                    onMouseEnter={(e) => {
                      if (!checked && !answeringQuestion)
                        e.currentTarget.style.background = "oklch(1 0 0 / 0.03)";
                    }}
                    onMouseLeave={(e) => {
                      if (!checked && !answeringQuestion)
                        e.currentTarget.style.background = "transparent";
                    }}
                  >
                    {/* Selected state: tiny accent left rail */}
                    {checked && (
                      <span
                        aria-hidden
                        className="absolute inset-y-0 left-0 w-[2px]"
                        style={{
                          background: "var(--color-accent)",
                          boxShadow: "0 0 8px var(--color-accent-glow)",
                        }}
                      />
                    )}

                    <div className="flex items-start gap-2.5 px-3 py-2">
                      {/* Custom indicator — replaces native radio/checkbox visuals */}
                      <span
                        className="mt-[3px] grid h-[14px] w-[14px] shrink-0 place-items-center"
                        style={{
                          borderRadius: currentQuestion.multiSelect ? "3px" : "9999px",
                          border: `1.5px solid ${checked ? "var(--color-accent)" : "var(--color-hairline-strong)"}`,
                          background: checked
                            ? "var(--color-accent)"
                            : "transparent",
                          transition: "all 0.15s ease",
                        }}
                      >
                        {checked && (
                          <span
                            className="block"
                            style={{
                              width: currentQuestion.multiSelect ? "8px" : "5px",
                              height: currentQuestion.multiSelect ? "8px" : "5px",
                              borderRadius: currentQuestion.multiSelect ? "1px" : "9999px",
                              background: "oklch(0.14 0 0)",
                            }}
                          />
                        )}
                      </span>

                      <input
                        type={currentQuestion.multiSelect ? "checkbox" : "radio"}
                        name={`assistant-question-${pendingQuestion.question_id}-${currentQuestionKey}`}
                        aria-label={option.label}
                        checked={checked}
                        disabled={answeringQuestion}
                        onChange={(event) => {
                          if (currentQuestion.multiSelect) {
                            toggleMultiQuestionAnswer(currentQuestionKey, option.value, event.target.checked);
                            return;
                          }
                          setSingleQuestionAnswer(currentQuestionKey, option.value);
                        }}
                        className="sr-only"
                      />

                      <div className="min-w-0 flex-1">
                        <div
                          className="text-[12.5px] font-medium leading-[1.4]"
                          style={{ color: checked ? "var(--color-text)" : "var(--color-text-2)" }}
                        >
                          {option.label}
                        </div>
                        {option.description && (
                          <div
                            className="mt-0.5 text-[11px] leading-[1.5]"
                            style={{ color: "var(--color-text-3)" }}
                          >
                            {option.description}
                          </div>
                        )}
                      </div>
                    </div>
                  </label>
                );
              })}
            </div>

          </section>
        )}

        {/* Other-input — anchored above the action bar; textarea grows upward
         * as the user types (max 160px, then internal scroll). Sits outside the
         * scrollable section so it stays visible while options scroll. */}
        {currentQuestion && isOtherSelected(currentQuestion, currentQuestionAnswer) && (
          <div className="shrink-0">
            <div
              className="mb-1 flex items-center gap-1.5 text-[10px] uppercase"
              style={{
                color: "var(--color-accent-2)",
                letterSpacing: "0.14em",
              }}
            >
              <span
                className="inline-block h-[2px] w-3 rounded-full"
                style={{ background: "var(--color-accent)" }}
              />
              {t("pending_question_wizard_other_label")}
            </div>
            <textarea
              ref={otherTextareaRef}
              value={currentQuestionCustomAnswer}
              onChange={(event) => {
                setCustomQuestionAnswer(currentQuestionKey, event.target.value);
                const el = event.currentTarget;
                el.style.height = "auto";
                const next = Math.min(el.scrollHeight, OTHER_TEXTAREA_MAX_PX);
                el.style.height = `${next}px`;
                el.style.overflowY =
                  el.scrollHeight > OTHER_TEXTAREA_MAX_PX ? "auto" : "hidden";
              }}
              placeholder={t("pending_question_wizard_other_placeholder")}
              aria-label={t("pending_question_wizard_other_label")}
              disabled={answeringQuestion}
              rows={2}
              className="w-full resize-none rounded-md px-3 py-2 text-[12.5px] leading-[1.55] outline-none transition-colors focus-ring"
              style={{
                border: "1px solid var(--color-accent-soft)",
                background: "oklch(0.16 0.010 265 / 0.7)",
                color: "var(--color-text)",
                maxHeight: `${OTHER_TEXTAREA_MAX_PX}px`,
                overflowY: "hidden",
              }}
            />
          </div>
        )}

        <div className="shrink-0 space-y-2">
          {error && (
            <div
              role="alert"
              aria-live="assertive"
              className="rounded-md px-2.5 py-1.5 text-[11.5px]"
              style={{
                border: "1px solid oklch(0.70 0.18 25 / 0.3)",
                background: "oklch(0.70 0.18 25 / 0.12)",
                color: "oklch(0.85 0.10 25)",
              }}
            >
              {error}
            </div>
          )}

          <div className="flex items-center justify-between gap-2">
            <button
              type="button"
              onClick={handlePreviousQuestion}
              disabled={answeringQuestion || isFirstQuestion}
              className="text-[12px] transition-colors focus-ring disabled:cursor-not-allowed disabled:opacity-30"
              style={{
                color: "var(--color-text-3)",
                letterSpacing: "0.04em",
              }}
              onMouseEnter={(e) => {
                if (!isFirstQuestion && !answeringQuestion)
                  e.currentTarget.style.color = "var(--color-text)";
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.color = "var(--color-text-3)";
              }}
            >
              {t("pending_question_wizard_prev")}
            </button>

            {isLastQuestion ? (
              <button
                type="submit"
                disabled={answeringQuestion || !allQuestionsReady}
                className="rounded-lg px-4 py-2 text-[12.5px] font-semibold transition-all focus-ring disabled:cursor-not-allowed disabled:opacity-40"
                style={{
                  color: "oklch(0.14 0 0)",
                  background:
                    "linear-gradient(180deg, var(--color-accent-2), var(--color-accent))",
                  boxShadow:
                    "inset 0 1px 0 oklch(1 0 0 / 0.35), 0 6px 18px -4px var(--color-accent-glow), 0 0 0 1px var(--color-accent-soft)",
                  letterSpacing: "0.04em",
                }}
              >
                {answeringQuestion
                  ? t("pending_question_wizard_submitting")
                  : t("pending_question_wizard_submit")}
              </button>
            ) : (
              <button
                type="button"
                onClick={handleNextQuestion}
                disabled={answeringQuestion || !currentQuestionReady}
                className="rounded-lg px-4 py-2 text-[12.5px] font-semibold transition-all focus-ring disabled:cursor-not-allowed disabled:opacity-40"
                style={{
                  color: "oklch(0.14 0 0)",
                  background:
                    "linear-gradient(180deg, var(--color-accent-2), var(--color-accent))",
                  boxShadow:
                    "inset 0 1px 0 oklch(1 0 0 / 0.35), 0 6px 18px -4px var(--color-accent-glow), 0 0 0 1px var(--color-accent-soft)",
                  letterSpacing: "0.04em",
                }}
              >
                {t("pending_question_wizard_next")}
              </button>
            )}
          </div>
        </div>
      </div>
    </form>
  );
}
