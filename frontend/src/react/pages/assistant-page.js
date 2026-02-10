import React, { useEffect, useMemo, useState } from "react";
import htm from "htm";

import { cn } from "../utils.js";
import { ChatMessage } from "../components/chat/index.js";
import { Badge, Button, Card } from "../components/primitives.js";

const html = htm.bind(React.createElement);

const SESSION_STATUS_META = {
    idle: {
        label: "空闲",
        className: "border-slate-500/30 bg-slate-500/10 text-slate-300",
    },
    running: {
        label: "进行中",
        className: "border-sky-400/35 bg-sky-500/15 text-sky-200 animate-pulse",
    },
    completed: {
        label: "已完成",
        className: "border-emerald-400/35 bg-emerald-500/15 text-emerald-200",
    },
    error: {
        label: "异常",
        className: "border-red-400/35 bg-red-500/15 text-red-200",
    },
    interrupted: {
        label: "已中断",
        className: "border-amber-400/35 bg-amber-500/15 text-amber-200",
    },
};

function getSessionStatusMeta(status) {
    if (typeof status !== "string") {
        return SESSION_STATUS_META.idle;
    }
    return SESSION_STATUS_META[status] || SESSION_STATUS_META.idle;
}

function buildSessionStatusTooltip(statusDetail, fallbackStatus) {
    const status = typeof statusDetail?.status === "string" && statusDetail.status
        ? statusDetail.status
        : fallbackStatus;
    const lines = [`status: ${status}`];
    if (statusDetail?.subtype) {
        lines.push(`subtype: ${statusDetail.subtype}`);
    }
    if (statusDetail?.stopReason) {
        lines.push(`stop_reason: ${statusDetail.stopReason}`);
    }
    if (typeof statusDetail?.isError === "boolean") {
        lines.push(`is_error: ${statusDetail.isError ? "true" : "false"}`);
    }
    if (statusDetail?.sessionId) {
        lines.push(`session_id: ${statusDetail.sessionId}`);
    }
    return lines.join("\n");
}

export function AssistantMessageArea({
    assistantCurrentSessionId,
    assistantSessions,
    assistantMessagesLoading,
    assistantComposedMessages,
    assistantError,
    assistantSkills,
    assistantSkillsLoading,
    assistantInput,
    setAssistantInput,
    assistantSending,
    assistantInterrupting,
    assistantPendingQuestion,
    assistantAnsweringQuestion,
    sessionStatus,
    sessionStatusDetail,
    onSendAssistantMessage,
    onInterruptAssistantSession,
    onAnswerAssistantQuestion,
    assistantChatScrollRef,
}) {
    const [activeSkillIndex, setActiveSkillIndex] = useState(0);
    const [questionAnswers, setQuestionAnswers] = useState({});
    const [questionCustomAnswers, setQuestionCustomAnswers] = useState({});

    const slashQuery = useMemo(() => {
        const raw = assistantInput || "";
        if (!raw.startsWith("/")) {
            return null;
        }

        if (/\s/.test(raw)) {
            return null;
        }

        return raw.slice(1).toLowerCase();
    }, [assistantInput]);

    const filteredSkills = useMemo(() => {
        if (slashQuery === null) {
            return [];
        }

        const skillList = Array.isArray(assistantSkills) ? assistantSkills : [];
        if (!slashQuery) {
            return skillList.slice(0, 8);
        }

        return skillList
            .filter((item) => {
                const name = (item.name || "").toLowerCase();
                const description = (item.description || "").toLowerCase();
                return name.includes(slashQuery) || description.includes(slashQuery);
            })
            .slice(0, 8);
    }, [assistantSkills, slashQuery]);

    useEffect(() => {
        setActiveSkillIndex(0);
    }, [slashQuery, filteredSkills.length, assistantSkillsLoading]);

    useEffect(() => {
        if (!assistantPendingQuestion || !Array.isArray(assistantPendingQuestion.questions)) {
            setQuestionAnswers({});
            setQuestionCustomAnswers({});
            return;
        }
        const initial = {};
        const initialCustom = {};
        assistantPendingQuestion.questions.forEach((question, index) => {
            const key = getQuestionKey(question, index);
            initial[key] = question?.multiSelect ? [] : "";
            initialCustom[key] = "";
        });
        setQuestionAnswers(initial);
        setQuestionCustomAnswers(initialCustom);
    }, [assistantPendingQuestion]);

    const showSkillPanel = slashQuery !== null;
    const hasPendingQuestion = !!assistantPendingQuestion;
    const isSessionRunning = sessionStatus === "running";

    const applySkill = (skillName) => {
        setAssistantInput(`/${skillName} `);
    };

    const handleInputKeyDown = (event) => {
        if (!showSkillPanel || filteredSkills.length === 0) {
            return;
        }

        if (event.key === "ArrowDown") {
            event.preventDefault();
            setActiveSkillIndex((previous) => (previous + 1) % filteredSkills.length);
            return;
        }

        if (event.key === "ArrowUp") {
            event.preventDefault();
            setActiveSkillIndex((previous) => (previous - 1 + filteredSkills.length) % filteredSkills.length);
            return;
        }

        if ((event.key === "Enter" && !event.shiftKey) || event.key === "Tab") {
            event.preventDefault();
            const skill = filteredSkills[activeSkillIndex];
            if (!skill?.name) {
                return;
            }
            applySkill(skill.name);
        }
    };

    const setSingleQuestionAnswer = (questionKey, label) => {
        setQuestionAnswers((previous) => ({
            ...previous,
            [questionKey]: label,
        }));
    };

    const toggleMultiQuestionAnswer = (questionKey, label, checked) => {
        setQuestionAnswers((previous) => {
            const current = Array.isArray(previous[questionKey]) ? previous[questionKey] : [];
            const next = checked
                ? Array.from(new Set([...current, label]))
                : current.filter((item) => item !== label);
            return {
                ...previous,
                [questionKey]: next,
            };
        });
    };

    const setCustomQuestionAnswer = (questionKey, value) => {
        setQuestionCustomAnswers((previous) => ({
            ...previous,
            [questionKey]: value,
        }));
    };

    const questionAnswersReady = useMemo(() => {
        if (!assistantPendingQuestion || !Array.isArray(assistantPendingQuestion.questions)) {
            return false;
        }
        return assistantPendingQuestion.questions.every((question, index) => {
            const key = getQuestionKey(question, index);
            const value = questionAnswers[key];
            if (question?.multiSelect) {
                if (!Array.isArray(value) || value.length === 0) {
                    return false;
                }
                if (!isOtherSelected(question, value)) {
                    return true;
                }
                return typeof questionCustomAnswers[key] === "string" && questionCustomAnswers[key].trim().length > 0;
            }
            if (!(typeof value === "string" && value.trim().length > 0)) {
                return false;
            }
            if (!isOtherSelected(question, value)) {
                return true;
            }
            return typeof questionCustomAnswers[key] === "string" && questionCustomAnswers[key].trim().length > 0;
        });
    }, [assistantPendingQuestion, questionAnswers, questionCustomAnswers]);

    const handleAnswerSubmit = (event) => {
        event.preventDefault();
        if (!assistantPendingQuestion) {
            return;
        }
        const answers = {};
        assistantPendingQuestion.questions.forEach((question, index) => {
            const questionKey = getQuestionKey(question, index);
            const answerKey = question?.question || questionKey;
            const value = questionAnswers[questionKey];
            if (question?.multiSelect) {
                if (Array.isArray(value) && value.length > 0) {
                    const normalizedValues = value
                        .map((item) => {
                            if (isOtherOptionValue(item)) {
                                return (questionCustomAnswers[questionKey] || "").trim();
                            }
                            return String(item || "").trim();
                        })
                        .filter(Boolean);
                    if (normalizedValues.length > 0) {
                        answers[answerKey] = normalizedValues.join(", ");
                    }
                }
                return;
            }
            if (typeof value === "string" && value.trim().length > 0) {
                const answerValue = isOtherOptionValue(value)
                    ? (questionCustomAnswers[questionKey] || "").trim()
                    : value.trim();
                if (answerValue) {
                    answers[answerKey] = answerValue;
                }
            }
        });
        onAnswerAssistantQuestion?.(assistantPendingQuestion.id, answers);
    };

    const currentSessionTitle =
        assistantSessions.find((session) => session.id === assistantCurrentSessionId)?.title ||
        assistantCurrentSessionId;
    const normalizedStatusDetail = sessionStatusDetail && typeof sessionStatusDetail === "object"
        ? sessionStatusDetail
        : {};
    const statusValue = typeof normalizedStatusDetail.status === "string" && normalizedStatusDetail.status
        ? normalizedStatusDetail.status
        : sessionStatus;
    const statusMeta = getSessionStatusMeta(statusValue);
    const statusBadgeTitle = buildSessionStatusTooltip(normalizedStatusDetail, statusValue);
    const statusBadgeClassName = cn(
        "text-[11px] px-2 py-0.5 rounded-full border shrink-0",
        statusMeta.className,
        normalizedStatusDetail.isError && statusValue !== "error" ? "ring-1 ring-red-300/40" : ""
    );

    return html`
        <div className="h-full min-h-0 flex flex-col rounded-xl border border-white/10 bg-ink-900/40 overflow-hidden">
            <div className="px-3 py-2 border-b border-white/10 flex items-center justify-between gap-2">
                <div className="text-xs text-slate-400 truncate">
                    ${assistantCurrentSessionId ? `会话：${currentSessionTitle}` : "请选择或创建会话"}
                </div>
                ${assistantCurrentSessionId
                    ? html`
                          <span className=${statusBadgeClassName} title=${statusBadgeTitle}>
                              ${statusMeta.label}
                          </span>
                      `
                    : null}
            </div>
            <div ref=${assistantChatScrollRef} className="flex-1 overflow-y-auto p-3 space-y-3">
                ${assistantMessagesLoading
                    ? html`<p className="text-sm text-slate-400">消息加载中...</p>`
                    : assistantComposedMessages.length === 0
                        ? html`<p className="text-sm text-slate-400">还没有消息，先发送一条吧。</p>`
                        : assistantComposedMessages.map((message, index) => html`
                              <${ChatMessage} key=${message.uuid || `turn-${index}`} message=${message} />
                          `)}
            </div>
            ${hasPendingQuestion
                ? html`
                      <form className="px-3 py-3 border-t border-amber-300/20 bg-amber-500/5 space-y-3" onSubmit=${handleAnswerSubmit}>
                          <div className="text-xs uppercase tracking-wide text-amber-300">
                              需要你的选择
                          </div>
                          ${(assistantPendingQuestion.questions || []).map((question, questionIndex) => {
                              const key = getQuestionKey(question, questionIndex);
                              const options = Array.isArray(question?.options) ? question.options : [];
                              const normalizedOptions = buildQuestionOptions(options);
                              const selected = questionAnswers[key];
                              return html`
                                  <section key=${`${assistantPendingQuestion.id}-${key}`} className="rounded-lg border border-amber-300/20 bg-ink-900/40 p-3">
                                      <div className="flex items-center gap-2 mb-2">
                                          ${question?.header
                                              ? html`<${Badge} className="bg-amber-300/15 text-amber-200">${question.header}<//>`
                                              : null}
                                          <span className="text-xs text-slate-400">
                                              ${question?.multiSelect ? "可多选" : "单选"}
                                          </span>
                                      </div>
                                      <p className="text-sm text-slate-100 mb-2">${question?.question || "请选择一个选项"}</p>
                                      <div className="space-y-2">
                                          ${normalizedOptions.map((option, optionIndex) => {
                                              const checked = question?.multiSelect
                                                  ? Array.isArray(selected) && selected.includes(option.value)
                                                  : selected === option.value;
                                              return html`
                                                  <label key=${`${key}-${optionIndex}`} className="block rounded-md border border-white/10 bg-white/5 px-3 py-2 cursor-pointer hover:bg-white/10">
                                                      <div className="flex items-start gap-2">
                                                          <input
                                                              type=${question?.multiSelect ? "checkbox" : "radio"}
                                                              name=${`assistant-question-${assistantPendingQuestion.id}-${key}`}
                                                              checked=${checked}
                                                              onChange=${(event) => {
                                                                  if (question?.multiSelect) {
                                                                      toggleMultiQuestionAnswer(key, option.value, event.target.checked);
                                                                  } else {
                                                                      setSingleQuestionAnswer(key, option.value);
                                                                  }
                                                              }}
                                                              className="mt-1"
                                                          />
                                                          <div>
                                                              <div className="text-sm text-slate-100">${option.label}</div>
                                                              ${option?.description
                                                                  ? html`<div className="text-xs text-slate-400 mt-1">${option.description}</div>`
                                                                  : null}
                                                          </div>
                                                      </div>
                                                  </label>
                                              `;
                                          })}
                                      </div>
                                      ${isOtherSelected(question, selected)
                                          ? html`
                                                <div className="mt-2">
                                                    <input
                                                        type="text"
                                                        value=${questionCustomAnswers[key] || ""}
                                                        onChange=${(event) => setCustomQuestionAnswer(key, event.target.value)}
                                                        placeholder="请输入其他内容"
                                                        className="w-full rounded-md border border-amber-300/30 bg-white/5 px-3 py-2 text-sm text-slate-100 placeholder:text-slate-500"
                                                    />
                                                </div>
                                            `
                                          : null}
                                  </section>
                              `;
                          })}
                          <div className="flex justify-end">
                              <${Button} type="submit" disabled=${assistantAnsweringQuestion || !questionAnswersReady}>
                                  ${assistantAnsweringQuestion ? "提交中..." : "提交答案"}
                              <//>
                          </div>
                      </form>
                  `
                : null}
            ${assistantError
                ? html`<div className="px-3 py-2 text-xs text-red-300 border-t border-red-400/20 bg-red-500/10">${assistantError}</div>`
                : null}
            <form className="p-3 border-t border-white/10 flex items-end gap-2" onSubmit=${onSendAssistantMessage}>
                <div className="relative flex-1">
                    ${showSkillPanel
                        ? html`
                              <div className="absolute bottom-full left-0 right-0 mb-2 rounded-xl border border-white/15 bg-ink-900/95 shadow-2xl overflow-hidden">
                                  <div className="px-3 py-2 text-xs text-slate-400 border-b border-white/10">可用 Skills</div>
                                  <div className="max-h-56 overflow-y-auto">
                                      ${assistantSkillsLoading
                                          ? html`<p className="px-3 py-3 text-sm text-slate-400">技能加载中...</p>`
                                          : filteredSkills.length === 0
                                              ? html`<p className="px-3 py-3 text-sm text-slate-400">没有匹配到技能</p>`
                                              : filteredSkills.map((skill, index) => html`
                                                    <button
                                                        type="button"
                                                        onMouseDown=${(event) => {
                                                            event.preventDefault();
                                                            applySkill(skill.name);
                                                        }}
                                                        className=${cn(
                                                            "w-full text-left px-3 py-2 border-b border-white/5 last:border-b-0",
                                                            index === activeSkillIndex
                                                                ? "bg-neon-500/15"
                                                                : "hover:bg-white/5"
                                                        )}
                                                    >
                                                        <div className="flex items-center justify-between gap-2">
                                                            <span className="font-medium text-sm text-slate-100">/${skill.name}</span>
                                                            <${Badge} className="bg-white/10 text-slate-300">${skill.scope || "project"}<//>
                                                        </div>
                                                        <p className="mt-1 text-xs text-slate-400 line-clamp-2">${skill.description || "无描述"}</p>
                                                    </button>
                                                `)}
                                  </div>
                              </div>
                          `
                        : null}
                    <textarea
                        value=${assistantInput}
                        onChange=${(event) => setAssistantInput(event.target.value)}
                        onKeyDown=${handleInputKeyDown}
                        rows="2"
                        placeholder=${hasPendingQuestion
                            ? "请先回答上方问题"
                            : isSessionRunning
                                ? "助手正在生成中，可点击停止中断"
                                : "输入消息，使用 /技能名 可指定技能"}
                        className="w-full rounded-xl border border-white/15 bg-ink-900/70 px-3 py-2 text-sm resize-none"
                        disabled=${assistantSending || hasPendingQuestion || isSessionRunning}
                    ></textarea>
                </div>
                ${isSessionRunning
                    ? html`
                          <${Button}
                              type="button"
                              variant="danger"
                              onClick=${onInterruptAssistantSession}
                              disabled=${assistantInterrupting}
                          >
                              ${assistantInterrupting ? "停止中..." : "停止"}
                          <//>
                      `
                    : html`
                          <${Button} type="submit" disabled=${assistantSending || hasPendingQuestion || !assistantInput.trim()}>
                              ${assistantSending ? "发送中" : "发送"}
                          <//>
                      `}
            </form>
        </div>
    `;
}

function getQuestionKey(question, index) {
    const rawQuestion = typeof question?.question === "string" ? question.question.trim() : "";
    if (rawQuestion) {
        return rawQuestion;
    }
    return `question_${index + 1}`;
}

const ASSISTANT_OTHER_OPTION_VALUE = "__assistant_option_other__";
const ASSISTANT_OTHER_OPTION_LABEL = "其他";

function isOtherOptionLabel(label) {
    const normalized = String(label || "").trim().toLowerCase();
    return normalized === "其他" || normalized === "other";
}

function isOtherOptionValue(value) {
    return value === ASSISTANT_OTHER_OPTION_VALUE;
}

function isOtherSelected(question, selected) {
    if (question?.multiSelect) {
        return Array.isArray(selected) && selected.includes(ASSISTANT_OTHER_OPTION_VALUE);
    }
    return selected === ASSISTANT_OTHER_OPTION_VALUE;
}

function buildQuestionOptions(options) {
    const normalized = options.map((option, index) => {
        const label = option?.label || `选项 ${index + 1}`;
        const isOther = isOtherOptionLabel(label);
        return {
            ...option,
            label,
            value: isOther ? ASSISTANT_OTHER_OPTION_VALUE : label,
            isOther,
        };
    });

    const hasOtherOption = normalized.some((option) => option.isOther);
    if (!hasOtherOption) {
        normalized.push({
            label: ASSISTANT_OTHER_OPTION_LABEL,
            description: "若以上选项都不符合，可自行输入",
            value: ASSISTANT_OTHER_OPTION_VALUE,
            isOther: true,
        });
    }

    return normalized;
}

export function AssistantPage({
    assistantLoadingSessions,
    assistantSessions,
    assistantCurrentSessionId,
    setAssistantCurrentSessionId,
    currentAssistantProject,
    projects,
    onAssistantScopeChange,
    onCreateSession,
    onRenameSession,
    onDeleteSession,
    messageArea,
}) {
    return html`
        <div className="h-full min-h-0 grid grid-cols-1 grid-rows-[220px_minmax(0,1fr)] lg:grid-rows-1 lg:grid-cols-[280px_minmax(0,1fr)] gap-3 overflow-hidden">
            <${Card} className="min-h-0 flex flex-col gap-2 overflow-hidden p-3">
                <div className="flex items-center justify-between">
                    <h3 className="font-semibold">会话列表</h3>
                    <${Button} size="sm" onClick=${onCreateSession}>新建<//>
                </div>

                <label className="text-sm text-slate-400">
                    项目范围
                    <select
                        value=${currentAssistantProject}
                        onChange=${(event) => onAssistantScopeChange(event.target.value)}
                        className="mt-1 w-full h-8 rounded-lg border border-white/15 bg-ink-900/70 px-2 text-xs text-slate-100"
                    >
                        <option value="">全部项目</option>
                        ${projects.map((project) => html`
                            <option key=${project.name} value=${project.name}>${project.title || project.name}</option>
                        `)}
                    </select>
                </label>

                <div className="flex-1 min-h-0 overflow-y-auto space-y-2">
                    ${assistantLoadingSessions
                        ? html`<p className="text-sm text-slate-400">会话加载中...</p>`
                        : assistantSessions.length === 0
                            ? html`<p className="text-sm text-slate-400">暂无会话</p>`
                            : assistantSessions.map((session) => html`
                                  <article
                                      key=${session.id}
                                      className=${cn(
                                          "rounded-xl border px-3 py-2",
                                          assistantCurrentSessionId === session.id
                                              ? "border-neon-400/40 bg-neon-500/10"
                                              : "border-white/10 bg-white/5"
                                      )}
                                  >
                                      <div className="flex items-center gap-2">
                                          <button
                                              onClick=${() => setAssistantCurrentSessionId(session.id)}
                                              title=${`${session.project_name} · ${session.status}`}
                                              className="min-w-0 flex-1 text-left"
                                          >
                                              <p className="text-sm font-medium truncate">
                                                  ${session.title || session.id.slice(0, 8)}
                                              </p>
                                          </button>
                                          <div className="flex items-center gap-1 shrink-0">
                                              <${Button} size="sm" variant="ghost" className="h-7 px-2" onClick=${() => onRenameSession(session)}>重命名<//>
                                              <${Button} size="sm" variant="danger" className="h-7 px-2" onClick=${() => onDeleteSession(session)}>删除<//>
                                          </div>
                                      </div>
                                  </article>
                              `)}
                </div>
            <//>

            ${messageArea}
        </div>
    `;
}

export function AssistantFloatingPanel({
    onOpenManage,
    onClose,
    currentAssistantProject,
    projects,
    onAssistantScopeChange,
    assistantCurrentSessionId,
    setAssistantCurrentSessionId,
    assistantSessions,
    onCreateSession,
    messageArea,
}) {
    return html`
        <section className="fixed right-5 bottom-24 z-50 w-[92vw] max-w-[420px] h-[72vh] app-panel-strong rounded-2xl p-3 flex flex-col gap-3">
            <div className="flex items-center justify-between gap-2">
                <div>
                    <p className="text-sm font-semibold">助手工作台</p>
                    <p className="text-xs text-slate-400">在当前项目中直接调度 Skills</p>
                </div>
                <div className="flex items-center gap-2">
                    <${Button} size="sm" variant="ghost" onClick=${onOpenManage}>管理会话<//>
                    <${Button} size="sm" variant="ghost" onClick=${onClose}>关闭<//>
                </div>
            </div>

            <label className="text-xs text-slate-400">
                项目上下文
                <select
                    value=${currentAssistantProject}
                    onChange=${(event) => onAssistantScopeChange(event.target.value)}
                    className="mt-1 w-full h-9 rounded-xl border border-white/15 bg-ink-900/70 px-3 text-slate-100"
                >
                    <option value="">全部项目</option>
                    ${projects.map((project) => html`
                        <option key=${project.name} value=${project.name}>${project.title || project.name}</option>
                    `)}
                </select>
            </label>

            <div className="flex items-center gap-2 text-xs">
                <select
                    value=${assistantCurrentSessionId}
                    onChange=${(event) => setAssistantCurrentSessionId(event.target.value)}
                    className="flex-1 h-8 rounded-lg border border-white/15 bg-ink-900/70 px-2"
                >
                    <option value="">选择会话</option>
                    ${assistantSessions.map((session) => html`
                        <option key=${session.id} value=${session.id}>${(session.title || session.id.slice(0, 8)) + ` · ${session.project_name}`}</option>
                    `)}
                </select>
                <${Button} size="sm" variant="outline" onClick=${onCreateSession}>新建<//>
            </div>

            ${messageArea}
        </section>
    `;
}
