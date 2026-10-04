import type { FormEvent } from "react";
import { Loader2 } from "lucide-react";
import { useTranslation } from "react-i18next";
import {
  Questionnaire,
  QuestionnaireActions,
  QuestionnaireChoice,
  QuestionnaireChoiceDescription,
  QuestionnaireChoices,
  QuestionnaireDescription,
  QuestionnaireError,
  QuestionnaireInput,
  QuestionnaireItem,
  QuestionnaireNext,
  QuestionnairePrevious,
  QuestionnaireProgress,
  QuestionnaireSubmit,
  QuestionnaireTitle,
} from "@/components/ui/questionnaire";
import type { PendingQuestion } from "@/types";

type Question = PendingQuestion["questions"][number];

// Agent 自带的「其他」选项由自由输入框代替，不再单独列出
function isOtherOption(label: string | undefined): boolean {
  const normalized = (label ?? "").trim().toLowerCase();
  return normalized === "其他" || normalized === "other";
}

function itemName(index: number): string {
  return `question-${index}`;
}

function choicesOf(question: Question) {
  return (Array.isArray(question.options) ? question.options : []).filter(
    (option) => option?.label && !isOtherOption(option.label),
  );
}

/** 按 Agent 提问的原文回传答案；多选与自由输入合并成一句，用逗号分隔。 */
export function collectAnswers(questions: Question[], form: FormData): Record<string, string> {
  const answers: Record<string, string> = {};
  questions.forEach((question, index) => {
    const values = form
      .getAll(itemName(index))
      .map((value) => (typeof value === "string" ? value.trim() : ""))
      .filter(Boolean);
    if (values.length === 0) return;
    const key = question.question?.trim() || `question_${index + 1}`;
    answers[key] = values.join(", ");
  });
  return answers;
}

interface AgentQuestionnaireProps {
  pendingQuestion: PendingQuestion;
  answering: boolean;
  error: string | null;
  onSubmit: (questionId: string, answers: Record<string, string>) => void;
}

// ---------------------------------------------------------------------------
// AgentQuestionnaire — Agent 提问时占用输入框的位置。
// 头部与底部按钮固定，只有题目区滚动；高度上限是 Agent 面板高度的 70%，
// 展开的待办清单再占去空间时继续压缩题目区。
// 数字键选择选项，「其他」写在每题末尾的输入框里。
// ---------------------------------------------------------------------------

export function AgentQuestionnaire({ pendingQuestion, answering, error, onSubmit }: AgentQuestionnaireProps) {
  const { t } = useTranslation("dashboard");
  const { questions } = pendingQuestion;
  if (questions.length === 0) return null;

  const items = questions.map((question, index) => ({
    name: itemName(index),
    required: true,
    choices: choicesOf(question).map((option) => ({ value: option.label })),
  }));

  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (answering) return;
    onSubmit(pendingQuestion.question_id, collectAnswers(questions, new FormData(event.currentTarget)));
  };

  return (
    <div className="flex max-h-[70cqh] min-h-0 flex-col border-t border-border px-3 pt-2.5 pb-3">
      <Questionnaire
        key={pendingQuestion.question_id}
        items={items}
        shortcuts="numbers"
        onSubmit={handleSubmit}
        aria-label={t("question_form_label")}
        className="min-h-0 flex-1"
      >
        <div className="flex shrink-0 items-center gap-2 text-xs">
          <span aria-hidden className="size-1.5 shrink-0 rounded-full bg-warn" />
          <span className="min-w-0 flex-1 truncate text-subtle-foreground">{t("question_waiting")}</span>
          <QuestionnaireProgress
            className="min-w-0"
            // 原语的进度名称与朗读文字写死为英文，按界面语言覆盖
            render={(props, state) => {
              const text = t("question_progress", { current: state.current, total: state.total });
              return (
                <div {...props} aria-label={t("question_progress_label")} aria-valuetext={text}>
                  {text}
                </div>
              );
            }}
          />
        </div>

        {/* 只有题目区滚动，头部与按钮始终可见 */}
        <div className="relative min-h-0 flex-1 overflow-y-auto overscroll-contain">
          {questions.map((question, index) => (
            <QuestionnaireItem key={itemName(index)} name={itemName(index)} multiple={question.multiSelect} required>
              <QuestionnaireTitle>{question.question}</QuestionnaireTitle>
              {question.multiSelect && <QuestionnaireDescription>{t("question_multi_select")}</QuestionnaireDescription>}
              <QuestionnaireChoices>
                {choicesOf(question).map((option) => (
                  <QuestionnaireChoice key={option.label} value={option.label}>
                    <span>{option.label}</span>
                    {option.description && <QuestionnaireChoiceDescription>{option.description}</QuestionnaireChoiceDescription>}
                  </QuestionnaireChoice>
                ))}
                <QuestionnaireInput aria-label={t("question_other_label")} placeholder={t("question_other_placeholder")} />
              </QuestionnaireChoices>
              <QuestionnaireError>{t("question_required")}</QuestionnaireError>
            </QuestionnaireItem>
          ))}
        </div>

        {error && (
          <p role="alert" className="shrink-0 text-xs text-destructive">
            {error}
          </p>
        )}

        <QuestionnaireActions className="shrink-0">
          <QuestionnairePrevious size="sm" variant="ghost">
            {t("question_previous")}
          </QuestionnairePrevious>
          <QuestionnaireNext size="sm">{t("question_next")}</QuestionnaireNext>
          <QuestionnaireSubmit size="sm" disabled={answering}>
            {answering && <Loader2 aria-hidden data-icon="inline-start" className="animate-spin" />}
            {answering ? t("question_submitting") : t("question_submit")}
          </QuestionnaireSubmit>
        </QuestionnaireActions>
      </Questionnaire>
    </div>
  );
}
