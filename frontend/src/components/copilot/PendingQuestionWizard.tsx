import { useEffect, useMemo, useState } from "react";
import type { PendingQuestion } from "@/types";
import { cn } from "./chat/utils";
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
  const pendingQuestions = pendingQuestion.questions ?? [];
  const [questionAnswers, setQuestionAnswers] = useState<Record<string, string | string[]>>({});
  const [questionCustomAnswers, setQuestionCustomAnswers] = useState<Record<string, string>>({});
  const [currentQuestionIndex, setCurrentQuestionIndex] = useState(0);
  const [visitedQuestionIndexes, setVisitedQuestionIndexes] = useState<number[]>([]);

  useEffect(() => {
    const initialAnswers: Record<string, string | string[]> = {};
    const initialCustomAnswers: Record<string, string> = {};

    pendingQuestions.forEach((question, index) => {
      const key = getQuestionKey(question, index);
      initialAnswers[key] = question.multiSelect ? [] : "";
      initialCustomAnswers[key] = "";
    });

    setQuestionAnswers(initialAnswers);
    setQuestionCustomAnswers(initialCustomAnswers);
    setCurrentQuestionIndex(0);
    setVisitedQuestionIndexes(pendingQuestions.length > 0 ? [0] : []);
  }, [pendingQuestion.question_id, pendingQuestions]);

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
      className="border-t border-amber-300/20 bg-gradient-to-b from-amber-500/8 to-transparent px-3 py-3"
      onSubmit={handleSubmit}
    >
      <div className="flex max-h-[min(34rem,52vh)] min-h-0 flex-col gap-3 rounded-xl border border-amber-300/20 bg-gray-950/60 p-3 shadow-[0_0_0_1px_rgba(251,191,36,0.04)]">
        <div className="shrink-0 text-[11px] font-semibold uppercase tracking-[0.18em] text-amber-300">
          需要你的选择
        </div>

        <div className="shrink-0 flex items-center gap-2 overflow-x-auto pb-1">
          {pendingQuestions.map((question, questionIndex) => {
            const isActiveStep = questionIndex === normalizedQuestionIndex;
            const isVisitedStep = isActiveStep || visitedQuestionIndexes.includes(questionIndex);

            return (
              <button
                key={`${pendingQuestion.question_id}-step-${questionIndex}`}
                type="button"
                onClick={() => handleSelectQuestionStep(questionIndex)}
                disabled={answeringQuestion || !isVisitedStep}
                className={cn(
                  "shrink-0 rounded-full border px-3 py-1 text-xs transition-colors",
                  isActiveStep
                    ? "border-amber-300/60 bg-amber-300/20 text-amber-100"
                    : isVisitedStep
                      ? "border-white/20 bg-white/5 text-slate-200 hover:bg-white/10"
                      : "cursor-not-allowed border-white/10 bg-white/5 text-slate-500",
                )}
              >
                {`${questionIndex + 1}. ${question.header || `问题 ${questionIndex + 1}`}`}
              </button>
            );
          })}
        </div>

        <p className="text-xs text-slate-400">
          {`问题 ${normalizedQuestionIndex + 1}/${totalQuestions}`}
        </p>

        {currentQuestion && (
          <section
            className="min-h-0 flex-1 overflow-y-auto rounded-xl border border-amber-300/20 bg-white/[0.03] p-3 pr-2"
            data-testid="pending-question-scroll-area"
          >
            <div className="mb-2 flex items-center gap-2">
              {currentQuestion.header && (
                <span className="rounded-full bg-amber-300/15 px-2 py-0.5 text-[11px] text-amber-200">
                  {currentQuestion.header}
                </span>
              )}
              <span className="text-xs text-slate-400">
                {currentQuestion.multiSelect ? "可多选" : "单选"}
              </span>
            </div>

            <p className="mb-3 text-sm leading-6 text-slate-100">
              {currentQuestion.question || "请选择一个选项"}
            </p>

            <div className="space-y-2">
              {currentQuestionOptions.map((option, optionIndex) => {
                const checked = currentQuestion.multiSelect
                  ? Array.isArray(currentQuestionAnswer) && currentQuestionAnswer.includes(option.value)
                  : currentQuestionAnswer === option.value;

                return (
                  <label
                    key={`${currentQuestionKey}-${optionIndex}`}
                    className="block cursor-pointer rounded-lg border border-white/10 bg-white/5 px-3 py-2 transition-colors hover:bg-white/8"
                  >
                    <div className="flex items-start gap-2">
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
                        className="mt-1"
                      />
                      <div className="min-w-0">
                        <div className="text-sm text-slate-100">{option.label}</div>
                        {option.description && (
                          <div className="mt-1 text-xs leading-5 text-slate-400">
                            {option.description}
                          </div>
                        )}
                      </div>
                    </div>
                  </label>
                );
              })}
            </div>

            {isOtherSelected(currentQuestion, currentQuestionAnswer) && (
              <div className="mt-3">
                <input
                  type="text"
                  value={currentQuestionCustomAnswer}
                  onChange={(event) => setCustomQuestionAnswer(currentQuestionKey, event.target.value)}
                  placeholder="请输入其他内容"
                  disabled={answeringQuestion}
                  className="w-full rounded-lg border border-amber-300/30 bg-white/5 px-3 py-2 text-sm text-slate-100 placeholder:text-slate-500 outline-none transition-colors focus:border-amber-300/60"
                />
              </div>
            )}
          </section>
        )}

        <div className="shrink-0 space-y-3">
          {error && (
            <div className="rounded-lg border border-red-500/20 bg-red-500/10 px-3 py-2 text-xs text-red-300">
              {error}
            </div>
          )}

          <div className="flex items-center justify-between gap-2">
            <button
              type="button"
              onClick={handlePreviousQuestion}
              disabled={answeringQuestion || isFirstQuestion}
              className="rounded-lg border border-white/10 px-3 py-2 text-sm text-slate-300 transition-colors hover:bg-white/5 disabled:cursor-not-allowed disabled:opacity-40"
            >
              上一步
            </button>

            {isLastQuestion ? (
              <button
                type="submit"
                disabled={answeringQuestion || !allQuestionsReady}
                className="rounded-lg bg-amber-300 px-3 py-2 text-sm font-medium text-gray-950 transition-colors hover:bg-amber-200 disabled:cursor-not-allowed disabled:bg-amber-300/40 disabled:text-gray-300"
              >
                {answeringQuestion ? "提交中..." : "完成并提交"}
              </button>
            ) : (
              <button
                type="button"
                onClick={handleNextQuestion}
                disabled={answeringQuestion || !currentQuestionReady}
                className="rounded-lg bg-white/10 px-3 py-2 text-sm font-medium text-slate-100 transition-colors hover:bg-white/15 disabled:cursor-not-allowed disabled:bg-white/5 disabled:text-slate-500"
              >
                下一题
              </button>
            )}
          </div>
        </div>
      </div>
    </form>
  );
}
