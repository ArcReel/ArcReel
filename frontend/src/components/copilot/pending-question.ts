import type { PendingQuestion } from "@/types";

export const ASSISTANT_OTHER_OPTION_VALUE = "__assistant_option_other__";
export const ASSISTANT_OTHER_OPTION_LABEL = "Other";

type Question = PendingQuestion["questions"][number];

export interface QuestionOption {
  label: string;
  description: string;
  value: string;
  isOther: boolean;
}

function isOtherOptionLabel(label: string | undefined): boolean {
  const normalized = String(label || "").trim().toLowerCase();
  return normalized === "other";
}

function isOtherOptionValue(value: string): boolean {
  return value === ASSISTANT_OTHER_OPTION_VALUE;
}

export function getQuestionKey(question: Question, index: number): string {
  const rawQuestion = typeof question?.question === "string" ? question.question.trim() : "";
  return rawQuestion || `question_${index + 1}`;
}

export function buildQuestionOptions(options: Question["options"]): QuestionOption[] {
  const normalized = (Array.isArray(options) ? options : []).map((option, index) => {
    const label = option?.label || `Option ${index + 1}`;
    const isOther = isOtherOptionLabel(label);
    return {
      label,
      description: option?.description || "",
      value: isOther ? ASSISTANT_OTHER_OPTION_VALUE : label,
      isOther,
    };
  });

  if (normalized.some((option) => option.isOther)) {
    return normalized;
  }

  return [
    ...normalized,
    {
      label: ASSISTANT_OTHER_OPTION_LABEL,
      description: "If none of the above options fit, enter your own",
      value: ASSISTANT_OTHER_OPTION_VALUE,
      isOther: true,
    },
  ];
}

export function isOtherSelected(question: Question, selectedValue: string | string[]): boolean {
  if (question.multiSelect) {
    return Array.isArray(selectedValue) && selectedValue.includes(ASSISTANT_OTHER_OPTION_VALUE);
  }
  return selectedValue === ASSISTANT_OTHER_OPTION_VALUE;
}

export function isQuestionAnswerReady(
  question: Question,
  selectedValue: string | string[],
  customValue: string,
): boolean {
  const normalizedCustomValue = customValue.trim();

  if (question.multiSelect) {
    if (!Array.isArray(selectedValue) || selectedValue.length === 0) {
      return false;
    }
    if (!isOtherSelected(question, selectedValue)) {
      return true;
    }
    return normalizedCustomValue.length > 0;
  }

  if (!(typeof selectedValue === "string" && selectedValue.trim().length > 0)) {
    return false;
  }
  if (!isOtherSelected(question, selectedValue)) {
    return true;
  }
  return normalizedCustomValue.length > 0;
}

export function buildAnswersPayload(
  questions: Question[],
  questionAnswers: Record<string, string | string[]>,
  customAnswers: Record<string, string>,
): Record<string, string> {
  const payload: Record<string, string> = {};

  for (const [index, question] of questions.entries()) {
    const questionKey = getQuestionKey(question, index);
    const answerKey = question.question || questionKey;
    const value = questionAnswers[questionKey];

    if (question.multiSelect) {
      if (!Array.isArray(value) || value.length === 0) {
        continue;
      }

      const normalizedValues = value
        .map((item) => {
          if (isOtherOptionValue(item)) {
            return (customAnswers[questionKey] || "").trim();
          }
          return String(item || "").trim();
        })
        .filter(Boolean);

      if (normalizedValues.length > 0) {
        payload[answerKey] = normalizedValues.join(", ");
      }
      continue;
    }

    if (!(typeof value === "string" && value.trim().length > 0)) {
      continue;
    }

    const answerValue = isOtherOptionValue(value)
      ? (customAnswers[questionKey] || "").trim()
      : value.trim();

    if (answerValue) {
      payload[answerKey] = answerValue;
    }
  }

  return payload;
}

export function getNextVisitedSteps(currentVisitedSteps: number[], nextIndex: number): number[] {
  return Array.from(new Set([...(Array.isArray(currentVisitedSteps) ? currentVisitedSteps : []), nextIndex]))
    .sort((left, right) => left - right);
}
