# Assistant Question Wizard Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 将助手“需要你的选择”区域改为逐题向导（顶部步骤条 + 单题作答），并在最后一题点击“完成并提交”时一次性提交所有答案。

**Architecture:** 保持后端批量提交接口不变，仅重构前端交互层。先抽出纯函数模块承载题目导航/校验/payload 组装逻辑并通过单测锁定行为，再改造 `AssistantMessageArea` 只渲染当前题并接入“上一步/下一题/完成并提交”流程。最后做回归验证，确保聊天主流程与会话状态不受影响。

**Tech Stack:** React 18 + HTM (`frontend/src/react/pages/assistant-page.js`), Node `node:test` + `assert`, Vite build。

---

**Execution skills to apply during implementation:** `@test-driven-development`, `@verification-before-completion`, `@requesting-code-review`

### Task 1: Baseline And Scope Guard

**Files:**
- Reference: `docs/plans/2026-02-10-assistant-question-wizard-design.md`
- Reference: `frontend/src/react/pages/assistant-page.js`
- Reference: `frontend/src/react/hooks/use-assistant-state.js`

**Step 1: 确认工作目录与分支状态**

Run: `pwd && git branch --show-current && git status --short`  
Expected: 位于仓库根目录；识别当前分支；只存在预期未跟踪文件（如本地配置文件）。

**Step 2: 运行现有前端基线测试**

Run: `node frontend/tests/landing-page.test.mjs && node frontend/tests/app-shell-floating-button.test.mjs`  
Expected: 两个测试均 PASS。

**Step 3: 记录本次改造边界**

在实现说明中明确：
- 不修改后端路由：`webui/server/routers/assistant.py`
- 不修改提交协议：`answers` 仍为整批对象
- UI 仅改 `AssistantMessageArea` 的 pending question 区域

**Step 4: 提交基线检查说明（可选）**

若需要留痕，创建简短 commit note；否则进入 Task 2。

### Task 2: Write Failing Tests For Wizard Logic (Pure Functions)

**Files:**
- Create: `frontend/tests/assistant-question-wizard.test.mjs`
- Target module (to be created in Task 3): `frontend/src/react/pages/assistant-question-wizard.js`

**Step 1: 写失败测试，先定义行为契约**

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
        header: "选择项目",
        question: "你想基于哪个项目继续？",
        multiSelect: false,
        options: [{ label: "test" }, { label: "创建新项目" }, { label: "其他" }],
    },
    {
        header: "视频内容",
        question: "你想制作什么内容？",
        multiSelect: true,
        options: [{ label: "使用已有素材" }, { label: "我来描述内容" }, { label: "其他" }],
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
    assert.equal(isQuestionAnswerReady(q1, ASSISTANT_OTHER_OPTION_VALUE, "自定义项目"), true);

    assert.equal(isQuestionAnswerReady(q2, [], ""), false);
    assert.equal(isQuestionAnswerReady(q2, ["使用已有素材"], ""), true);
    assert.equal(isQuestionAnswerReady(q2, [ASSISTANT_OTHER_OPTION_VALUE], ""), false);
    assert.equal(isQuestionAnswerReady(q2, [ASSISTANT_OTHER_OPTION_VALUE], "自定义内容"), true);

    assert.equal(q1Key.length > 0, true);
    assert.equal(q2Key.length > 0, true);
});

test("buildAnswersPayload should map other values to custom text", () => {
    const questionAnswers = {
        [getQuestionKey(questions[0], 0)]: ASSISTANT_OTHER_OPTION_VALUE,
        [getQuestionKey(questions[1], 1)]: ["使用已有素材", ASSISTANT_OTHER_OPTION_VALUE],
    };
    const customAnswers = {
        [getQuestionKey(questions[0], 0)]: "我的旧项目",
        [getQuestionKey(questions[1], 1)]: "补充镜头需求",
    };
    const payload = buildAnswersPayload(questions, questionAnswers, customAnswers);

    assert.deepEqual(payload, {
        "你想基于哪个项目继续？": "我的旧项目",
        "你想制作什么内容？": "使用已有素材, 补充镜头需求",
    });
});

test("getNextVisitedSteps should keep unique and sorted visited indexes", () => {
    assert.deepEqual(getNextVisitedSteps([0], 1), [0, 1]);
    assert.deepEqual(getNextVisitedSteps([0, 1], 1), [0, 1]);
    assert.deepEqual(getNextVisitedSteps([0, 2], 1), [0, 1, 2]);
});
```

**Step 2: 运行测试确认失败**

Run: `node frontend/tests/assistant-question-wizard.test.mjs`  
Expected: FAIL，报 `assistant-question-wizard.js` 模块不存在或导出缺失。

**Step 3: 提交失败测试（红灯）**

```bash
git add frontend/tests/assistant-question-wizard.test.mjs
git commit -m "test(assistant): add failing wizard logic contract tests"
```

### Task 3: Implement Wizard Pure Logic Module (Make Task 2 Pass)

**Files:**
- Create: `frontend/src/react/pages/assistant-question-wizard.js`
- Test: `frontend/tests/assistant-question-wizard.test.mjs`

**Step 1: 实现最小逻辑函数（仅满足测试）**

```js
export const ASSISTANT_OTHER_OPTION_VALUE = "__assistant_option_other__";
export const ASSISTANT_OTHER_OPTION_LABEL = "其他";

export function getQuestionKey(question, index) {
    const rawQuestion = typeof question?.question === "string" ? question.question.trim() : "";
    return rawQuestion || `question_${index + 1}`;
}

function isOtherOptionLabel(label) {
    const normalized = String(label || "").trim().toLowerCase();
    return normalized === "其他" || normalized === "other";
}

export function buildQuestionOptions(options) {
    const normalized = (Array.isArray(options) ? options : []).map((option, index) => {
        const label = option?.label || `选项 ${index + 1}`;
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
            description: "若以上选项都不符合，可自行输入",
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

**Step 2: 运行测试确认通过**

Run: `node frontend/tests/assistant-question-wizard.test.mjs`  
Expected: PASS。

**Step 3: 做一次快速静态回归**

Run: `node frontend/tests/landing-page.test.mjs && node frontend/tests/app-shell-floating-button.test.mjs`  
Expected: PASS，无回归。

**Step 4: 提交最小实现（绿灯）**

```bash
git add frontend/src/react/pages/assistant-question-wizard.js frontend/tests/assistant-question-wizard.test.mjs
git commit -m "feat(assistant): add question wizard pure logic module"
```

### Task 4: Write Failing UI Regression Test For Single-Question Wizard

**Files:**
- Create: `frontend/tests/assistant-message-area-wizard.test.mjs`
- Target component: `frontend/src/react/pages/assistant-page.js`

**Step 1: 先写失败的 UI 约束测试**

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
            assistantSessions: [{ id: "session-1", title: "test 会话" }],
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
                        header: "选择项目",
                        question: "问题A：选项目",
                        multiSelect: false,
                        options: [{ label: "test" }, { label: "创建新项目" }],
                    },
                    {
                        header: "视频内容",
                        question: "问题B：选内容",
                        multiSelect: false,
                        options: [{ label: "使用已有素材" }, { label: "我来描述内容" }],
                    },
                ],
            },
            ...extra,
        })
    );
}

test("pending question area should render wizard progress and only current question", () => {
    const html = renderArea();

    assert.ok(html.includes("问题 1/2"));
    assert.ok(html.includes("下一题"));
    assert.ok(!html.includes("提交答案"));
    assert.ok(html.includes("问题A：选项目"));
    assert.ok(!html.includes("问题B：选内容"));
});
```

**Step 2: 运行测试确认失败**

Run: `node frontend/tests/assistant-message-area-wizard.test.mjs`  
Expected: FAIL（当前实现会显示全部问题，并出现“提交答案”按钮）。

**Step 3: 提交失败测试**

```bash
git add frontend/tests/assistant-message-area-wizard.test.mjs
git commit -m "test(assistant): add failing single-question wizard rendering test"
```

### Task 5: Refactor AssistantMessageArea To Step Wizard (Make Task 4 Pass)

**Files:**
- Modify: `frontend/src/react/pages/assistant-page.js`
- Import from: `frontend/src/react/pages/assistant-question-wizard.js`
- Test: `frontend/tests/assistant-message-area-wizard.test.mjs`

**Step 1: 引入 wizard 状态和 helper**

在 `AssistantMessageArea` 增加：
- `currentQuestionIndex`（number）
- `visitedSteps`（number[]）
- `currentQuestionReady`（当前题可推进）
- `isLastQuestion`（末题判断）

并从 helper 模块引入：
- `getQuestionKey`
- `buildQuestionOptions`
- `isOtherSelected`
- `isQuestionAnswerReady`
- `buildAnswersPayload`
- `getNextVisitedSteps`

**Step 2: 将 pending 区域改为“步骤条 + 单题卡片”**

核心渲染形态：

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
                {`${index + 1}. ${question?.header || `问题 ${index + 1}`}`}
            </button>
        );
    })}
</div>

<p className="text-xs text-slate-400">{`问题 ${currentQuestionIndex + 1}/${totalQuestions}`}</p>
```

只渲染 `currentQuestion` 的选项卡，不再 `map` 全部题目。

**Step 3: 绑定“上一步/下一题/完成并提交”动作**

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

按钮规则：
- 首题禁用 `上一步`
- 非末题显示 `下一题`（当前题无效时禁用）
- 末题显示 `完成并提交`（全量无效或提交中时禁用）

**Step 4: 确保 question 切换重置逻辑正确**

当 `assistantPendingQuestion` 变化时：
- 重置 `questionAnswers` 和 `questionCustomAnswers`
- `setCurrentQuestionIndex(0)`
- `setVisitedSteps([0])`

**Step 5: 跑测试并修复到全绿**

Run:
- `node frontend/tests/assistant-message-area-wizard.test.mjs`
- `node frontend/tests/assistant-question-wizard.test.mjs`
- `node frontend/tests/landing-page.test.mjs`
- `node frontend/tests/app-shell-floating-button.test.mjs`

Expected: 全部 PASS。

**Step 6: 提交 UI 改造**

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

**Step 1: 运行前端构建验证**

Run: `npm --prefix frontend run build`  
Expected: `vite build` 成功，无语法错误。

**Step 2: 再次运行完整相关测试**

Run:
```bash
node frontend/tests/assistant-question-wizard.test.mjs
node frontend/tests/assistant-message-area-wizard.test.mjs
node frontend/tests/landing-page.test.mjs
node frontend/tests/app-shell-floating-button.test.mjs
```

Expected: 全部 PASS。

**Step 3: 自检改造是否符合设计文档**

对照 `docs/plans/2026-02-10-assistant-question-wizard-design.md`，逐项确认：
- 逐题展示
- 顶部横向步骤条
- 可回退修改
- 末题“完成并提交”
- 后端接口不变

**Step 4: 终态提交**

```bash
git add frontend/src/react/pages/assistant-page.js frontend/src/react/pages/assistant-question-wizard.js frontend/tests/assistant-question-wizard.test.mjs frontend/tests/assistant-message-area-wizard.test.mjs
git commit -m "feat(assistant): implement step wizard for pending question flow"
```

**Step 5: 请求代码评审**

使用 `@requesting-code-review` 对最终差异做一次 review，请求重点检查：
- 回退修改是否会产生 payload 异常
- “其他”选项在单选/多选两类题目的边界行为
- 提交中状态是否彻底阻断重复触发
