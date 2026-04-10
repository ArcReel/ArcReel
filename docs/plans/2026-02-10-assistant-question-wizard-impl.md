# Assistant Question Wizard Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Change the assistant's “Your Choices Needed” area to a step-by-step wizard (top progress bar + one question at a time), and submit all answers at once when clicking “Finish & Submit” on the final question.

**Architecture:** Keep backend batch submit interface unchanged; only refactor the frontend interaction layer. Extract a pure function module for question navigation/validation/payload assembly logic and lock behavior via unit tests, then refactor `AssistantMessageArea` to render only the current question and integrate the “Previous/Next Question/Finish & Submit” flow. Finally do regression verification to ensure the main chat flow and session state are unaffected.

**Tech Stack:** React 18 + HTM (`frontend/src/react/pages/assistant-page.js`), Node `node:test` + `assert`, Vite build.

---

**Execution skills to apply during implementation:** `@test-driven-development`, `@verification-before-completion`, `@requesting-code-review`

### Task 1: Baseline And Scope Guard

**Files:**
- Reference: `docs/plans/2026-02-10-assistant-question-wizard-design.md`
- Reference: `frontend/src/react/pages/assistant-page.js`
- Reference: `frontend/src/react/hooks/use-assistant-state.js`

**Step 1: Confirm working directory and branch status**

Run: `pwd && git branch --show-current && git status --short`  
Expected: In repo root; identifies current branch; only expected untracked files (e.g., local config files) exist.

**Step 2: Run existing frontend baseline tests**

Run: `node frontend/tests/landing-page.test.mjs && node frontend/tests/app-shell-floating-button.test.mjs`  
Expected: Both tests PASS.

**Step 3: Document the scope of this refactor**

Clarify in the implementation notes:
- Do not modify backend routes: `webui/server/routers/assistant.py`
- Do not modify submission protocol: `answers` remains a batch object
- UI only changes the pending question area of `AssistantMessageArea`

**Step 4: Commit baseline check notes (optional)**

If a record is needed, create a brief commit note; otherwise proceed to Task 2.

### Task 2: Write Failing Tests For Wizard Logic (Pure Functions)

**Files:**
- Create: `frontend/tests/assistant-question-wizard.test.mjs`
- Target module (to be created in Task 3): `frontend/src/react/pages/assistant-question-wizard.js`

**Step 1: Write failing tests to define behavioral contract first**

```js
import test from "node:test";
import assert from "node:assert/strict";

import {
    ASSISTANT_OTHER_OPTION_VALUE,
    getQuestionKey,
    buildQuestionOptions,
    isQuestionAnswerReady,
    buildAnswersPayload,
    getNextVisitedSteps,
} from "../src/react/pages/assistant-question-wizard.js";

const questions = [
    {
        header: "Select Project",
        question: "Which project do you want to continue with?",
        multiSelect: false,
        options: [{ label: "test" }, { label: "Create New Project" }, { label: "Other" }],
    },
    {
        header: "Video Content",
        question: "What content do you want to create?",
        multiSelect: true,
        options: [{ label: "Use Existing Assets" }, { label: "I will describe the content" }, { label: "Other" }],
    },
];

test("buildQuestionOptions should normalize and keep a stable other value", () => {
    const normalized = buildQuestionOptions(questions[0].options);
    assert.equal(normalized[2].value, ASSISTANT_OTHER_OPTION_VALUE);
});

test("isQuestionAnswerReady should validate single and multi question answers", () => {
    const q1 = questions[0];
    const q2 = questions[1];
    const q1Key = getQuestionKey(q1, 0);
    const q2Key = getQuestionKey(q2, 1);

    assert.equal(isQuestionAnswerReady(q1, "", ""), false);
    assert.equal(isQuestionAnswerReady(q1, "test", ""), true);
    assert.equal(isQuestionAnswerReady(q1, ASSISTANT_OTHER_OPTION_VALUE, ""), false);
    assert.equal(isQuestionAnswerReady(q1, ASSISTANT_OTHER_OPTION_VALUE, "custom project"), true);

    assert.equal(isQuestionAnswerReady(q2, [], ""), false);
    assert.equal(isQuestionAnswerReady(q2, ["Use Existing Assets"], ""), true);
    assert.equal(isQuestionAnswerReady(q2, [ASSISTANT_OTHER_OPTION_VALUE], ""), false);
    assert.equal(isQuestionAnswerReady(q2, [ASSISTANT_OTHER_OPTION_VALUE], "custom content"), true);

    assert.equal(q1Key.length > 0, true);
    assert.equal(q2Key.length > 0, true);
});

test("buildAnswersPayload should map other values to custom text", () => {
    const questionAnswers = {
        [getQuestionKey(questions[0], 0)]: ASSISTANT_OTHER_OPTION_VALUE,
        [getQuestionKey(questions[1], 1)]: ["Use Existing Assets", ASSISTANT_OTHER_OPTION_VALUE],
    };
    const customAnswers = {
        [getQuestionKey(questions[0], 0)]: "my old project",
        [getQuestionKey(questions[1], 1)]: "additional shot requirements",
    };
    const payload = buildAnswersPayload(questions, questionAnswers, customAnswers);

    assert.deepEqual(payload, {
        "Which project do you want to continue with?": "my old project",
        "What content do you want to create?": "Use Existing Assets, additional shot requirements",
    });
});

test("getNextVisitedSteps should keep unique and sorted visited indexes", () => {
    assert.deepEqual(getNextVisitedSteps([0], 1), [0, 1]);
    assert.deepEqual(getNextVisitedSteps([0, 1], 1), [0, 1]);
    assert.deepEqual(getNextVisitedSteps([0, 2], 1), [0, 1, 2]);
});
```

**Step 2: Run tests to confirm they fail**

Run: `node frontend/tests/assistant-question-wizard.test.mjs`  
Expected: FAIL — reports that `assistant-question-wizard.js` module does not exist or exports are missing.

**Step 3: Commit failing tests (red)**

```bash
git add frontend/tests/assistant-question-wizard.test.mjs
git commit -m "test(assistant): add failing wizard logic contract tests"
```

### Task 3: Implement Wizard Pure Logic Module (Make Task 2 Pass)

**Files:**
- Create: `frontend/src/react/pages/assistant-question-wizard.js`
- Test: `frontend/tests/assistant-question-wizard.test.mjs`

**Step 1: Implement minimum logic functions (just enough to pass tests)**

```js
export const ASSISTANT_OTHER_OPTION_VALUE = "__assistant_option_other__";
export const ASSISTANT_OTHER_OPTION_LABEL = "Other";

export function getQuestionKey(question, index) {
    const rawQuestion = typeof question?.question === "string" ? question.question.trim() : "";
    return rawQuestion || `question_${index + 1}`;
}

function isOtherOptionLabel(label) {
    const normalized = String(label || "").trim().toLowerCase();
    return normalized === "other";
}

export function buildQuestionOptions(options) {
    const normalized = (Array.isArray(options) ? options : []).map((option, index) => {
        const label = option?.label || `Option ${index + 1}`;
        const isOther = isOtherOptionLabel(label);
        return {
            ...option,
            label,
            value: isOther ? ASSISTANT_OTHER_OPTION_VALUE : label,
            isOther,
        };
    });

    if (!normalized.some((item) => item.isOther)) {
        normalized.push({
            label: ASSISTANT_OTHER_OPTION_LABEL,
            description: "If none of the above options fit, you can enter your own",
            value: ASSISTANT_OTHER_OPTION_VALUE,
            isOther: true,
        });
    }

    return normalized;
}

export function isOtherSelected(question, selectedValue) {
    if (question?.multiSelect) {
        return Array.isArray(selectedValue) && selectedValue.includes(ASSISTANT_OTHER_OPTION_VALUE);
    }
    return selectedValue === ASSISTANT_OTHER_OPTION_VALUE;
}

export function isQuestionAnswerReady(question, selectedValue, customValue) {
    if (question?.multiSelect) {
        if (!Array.isArray(selectedValue) || selectedValue.length === 0) return false;
        if (!isOtherSelected(question, selectedValue)) return true;
        return typeof customValue === "string" && customValue.trim().length > 0;
    }

    if (!(typeof selectedValue === "string" && selectedValue.trim().length > 0)) return false;
    if (!isOtherSelected(question, selectedValue)) return true;
    return typeof customValue === "string" && customValue.trim().length > 0;
}

export function buildAnswersPayload(questions, questionAnswers, customAnswers) {
    const payload = {};
    (Array.isArray(questions) ? questions : []).forEach((question, index) => {
        const questionKey = getQuestionKey(question, index);
        const answerKey = question?.question || questionKey;
        const value = questionAnswers[questionKey];

        if (question?.multiSelect) {
            if (!Array.isArray(value) || value.length === 0) return;
            const normalizedValues = value
                .map((item) => (item === ASSISTANT_OTHER_OPTION_VALUE ? (customAnswers[questionKey] || "").trim() : String(item || "").trim()))
                .filter(Boolean);
            if (normalizedValues.length > 0) {
                payload[answerKey] = normalizedValues.join(", ");
            }
            return;
        }

        if (!(typeof value === "string" && value.trim().length > 0)) return;
        const answerValue = value === ASSISTANT_OTHER_OPTION_VALUE ? (customAnswers[questionKey] || "").trim() : value.trim();
        if (answerValue) {
            payload[answerKey] = answerValue;
        }
    });
    return payload;
}

export function getNextVisitedSteps(currentVisitedSteps, nextIndex) {
    return Array.from(new Set([...(Array.isArray(currentVisitedSteps) ? currentVisitedSteps : []), nextIndex])).sort((a, b) => a - b);
}
```

**Step 2: Run tests to confirm they pass**

Run: `node frontend/tests/assistant-question-wizard.test.mjs`  
Expected: PASS.

**Step 3: Do a quick static regression**

Run: `node frontend/tests/landing-page.test.mjs && node frontend/tests/app-shell-floating-button.test.mjs`  
Expected: PASS, no regressions.

**Step 4: Commit minimum implementation (green)**

```bash
git add frontend/src/react/pages/assistant-question-wizard.js frontend/tests/assistant-question-wizard.test.mjs
git commit -m "feat(assistant): add question wizard pure logic module"
```

### Task 4: Write Failing UI Regression Test For Single-Question Wizard

**Files:**
- Create: `frontend/tests/assistant-message-area-wizard.test.mjs`
- Target component: `frontend/src/react/pages/assistant-page.js`

**Step 1: Write failing UI constraint tests first**

```js
import test from "node:test";
import assert from "node:assert/strict";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { AssistantMessageArea } from "../src/react/pages/assistant-page.js";

function renderArea(extra = {}) {
    return renderToStaticMarkup(
        React.createElement(AssistantMessageArea, {
            assistantCurrentSessionId: "session-1",
            assistantSessions: [{ id: "session-1", title: "test session" }],
            assistantMessagesLoading: false,
            assistantComposedMessages: [],
            assistantError: "",
            assistantSkills: [],
            assistantSkillsLoading: false,
            assistantInput: "",
            setAssistantInput: () => {},
            assistantSending: false,
            assistantInterrupting: false,
            assistantAnsweringQuestion: false,
            sessionStatus: "idle",
            sessionStatusDetail: { status: "idle" },
            onSendAssistantMessage: () => {},
            onInterruptAssistantSession: () => {},
            onAnswerAssistantQuestion: () => {},
            assistantChatScrollRef: { current: null },
            assistantPendingQuestion: {
                id: "q-1",
                questions: [
                    {
                        header: "Select Project",
                        question: "Question A: Select project",
                        multiSelect: false,
                        options: [{ label: "test" }, { label: "Create New Project" }],
                    },
                    {
                        header: "Video Content",
                        question: "Question B: Select content",
                        multiSelect: false,
                        options: [{ label: "Use Existing Assets" }, { label: "I will describe the content" }],
                    },
                ],
            },
            ...extra,
        })
    );
}

test("pending question area should render wizard progress and only current question", () => {
    const html = renderArea();

    assert.ok(html.includes("Question 1/2"));
    assert.ok(html.includes("Next Question"));
    assert.ok(!html.includes("Submit Answers"));
    assert.ok(html.includes("Question A: Select project"));
    assert.ok(!html.includes("Question B: Select content"));
});
```

**Step 2: Run tests to confirm they fail**

Run: `node frontend/tests/assistant-message-area-wizard.test.mjs`  
Expected: FAIL (current implementation shows all questions and has a “Submit Answers” button).

**Step 3: Commit failing tests**

```bash
git add frontend/tests/assistant-message-area-wizard.test.mjs
git commit -m "test(assistant): add failing single-question wizard rendering test"
```

### Task 5: Refactor AssistantMessageArea To Step Wizard (Make Task 4 Pass)

**Files:**
- Modify: `frontend/src/react/pages/assistant-page.js`
- Import from: `frontend/src/react/pages/assistant-question-wizard.js`
- Test: `frontend/tests/assistant-message-area-wizard.test.mjs`

**Step 1: Import wizard state and helpers**

Add to `AssistantMessageArea`:
- `currentQuestionIndex` (number)
- `visitedSteps` (number[])
- `currentQuestionReady` (current question can advance)
- `isLastQuestion` (whether it's the last question)

And import from the helper module:
- `getQuestionKey`
- `buildQuestionOptions`
- `isOtherSelected`
- `isQuestionAnswerReady`
- `buildAnswersPayload`
- `getNextVisitedSteps`

**Step 2: Change the pending area to “progress bar + single question card”**

Core rendering structure:

```js
const totalQuestions = assistantPendingQuestion?.questions?.length || 0;
const currentQuestion = totalQuestions > 0 ? assistantPendingQuestion.questions[currentQuestionIndex] : null;

<div className="flex items-center gap-2 overflow-x-auto pb-1">
    {assistantPendingQuestion.questions.map((question, index) => {
        const active = index === currentQuestionIndex;
        const visited = visitedSteps.includes(index);
        return (
            <button
                type="button"
                disabled={assistantAnsweringQuestion || !visited}
                onClick={() => setCurrentQuestionIndex(index)}
                className={cn("shrink-0 rounded-full px-3 py-1 text-xs border", active ? "border-amber-300/60 bg-amber-300/20 text-amber-100" : "border-white/15 bg-white/5 text-slate-300")}
            >
                {`${index + 1}. ${question?.header || `Question ${index + 1}`}`}
            </button>
        );
    })}
</div>

<p className="text-xs text-slate-400">{`Question ${currentQuestionIndex + 1}/${totalQuestions}`}</p>
```

Only render the `currentQuestion` option card; no longer `map` over all questions.

**Step 3: Bind “Previous/Next Question/Finish & Submit” actions**

```js
const handlePrev = () => {
    setCurrentQuestionIndex((prev) => Math.max(0, prev - 1));
};

const handleNext = () => {
    setCurrentQuestionIndex((prev) => {
        const next = Math.min(totalQuestions - 1, prev + 1);
        setVisitedSteps((visited) => getNextVisitedSteps(visited, next));
        return next;
    });
};

const handleFinalSubmit = (event) => {
    event.preventDefault();
    const answers = buildAnswersPayload(assistantPendingQuestion.questions, questionAnswers, questionCustomAnswers);
    onAnswerAssistantQuestion?.(assistantPendingQuestion.id, answers);
};
```

Button rules:
- `Previous` disabled on first question
- Show `Next Question` on non-last questions (disabled when current question is invalid)
- Show `Finish & Submit` on last question (disabled when invalid or submitting)

**Step 4: Ensure question switch reset logic is correct**

When `assistantPendingQuestion` changes:
- Reset `questionAnswers` and `questionCustomAnswers`
- `setCurrentQuestionIndex(0)`
- `setVisitedSteps([0])`

**Step 5: Run tests and fix until all green**

Run:
- `node frontend/tests/assistant-message-area-wizard.test.mjs`
- `node frontend/tests/assistant-question-wizard.test.mjs`
- `node frontend/tests/landing-page.test.mjs`
- `node frontend/tests/app-shell-floating-button.test.mjs`

Expected: All PASS.

**Step 6: Commit UI refactor**

```bash
git add frontend/src/react/pages/assistant-page.js frontend/tests/assistant-message-area-wizard.test.mjs
git commit -m "feat(assistant): switch pending question UI to step-by-step wizard"
```

### Task 6: Final Verification, Build, And Handoff

**Files:**
- Verify: `frontend/src/react/pages/assistant-page.js`
- Verify: `frontend/src/react/pages/assistant-question-wizard.js`
- Verify: `frontend/tests/assistant-question-wizard.test.mjs`
- Verify: `frontend/tests/assistant-message-area-wizard.test.mjs`

**Step 1: Run frontend build verification**

Run: `npm --prefix frontend run build`  
Expected: `vite build` succeeds with no syntax errors.

**Step 2: Run the full related test suite again**

Run:
```bash
node frontend/tests/assistant-question-wizard.test.mjs
node frontend/tests/assistant-message-area-wizard.test.mjs
node frontend/tests/landing-page.test.mjs
node frontend/tests/app-shell-floating-button.test.mjs
```

Expected: All PASS.

**Step 3: Self-check that the refactor matches the design document**

Verify against `docs/plans/2026-02-10-assistant-question-wizard-design.md`, item by item:
- One question at a time
- Top horizontal progress bar
- Can go back and edit
- Last question has “Finish & Submit”
- Backend interface unchanged

**Step 4: Final commit**

```bash
git add frontend/src/react/pages/assistant-page.js frontend/src/react/pages/assistant-question-wizard.js frontend/tests/assistant-question-wizard.test.mjs frontend/tests/assistant-message-area-wizard.test.mjs
git commit -m “feat(assistant): implement step wizard for pending question flow”
```

**Step 5: Request code review**

Use `@requesting-code-review` to review the final diff, with focus on:
- Whether going back and editing could produce payload anomalies
- Edge behavior of the “Other” option in both single-choice and multi-choice questions
- Whether the “submitting” state fully prevents duplicate triggers
