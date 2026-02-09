import React, { useEffect, useMemo, useState } from "react";
import htm from "htm";

import { cn } from "../utils.js";
import { ChatMessage } from "../components/chat/index.js";
import { Badge, Button, Card } from "../components/primitives.js";

const html = htm.bind(React.createElement);

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
    onSendAssistantMessage,
    assistantChatScrollRef,
}) {
    const [activeSkillIndex, setActiveSkillIndex] = useState(0);

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

    const showSkillPanel = slashQuery !== null;

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

    const currentSessionTitle =
        assistantSessions.find((session) => session.id === assistantCurrentSessionId)?.title ||
        assistantCurrentSessionId;

    return html`
        <div className="h-full min-h-0 flex flex-col rounded-xl border border-white/10 bg-ink-900/40 overflow-hidden">
            <div className="px-3 py-2 border-b border-white/10 text-xs text-slate-400">
                ${assistantCurrentSessionId ? `会话：${currentSessionTitle}` : "请选择或创建会话"}
            </div>
            <div ref=${assistantChatScrollRef} className="flex-1 overflow-y-auto p-3 space-y-3">
                ${assistantMessagesLoading
                    ? html`<p className="text-sm text-slate-400">消息加载中...</p>`
                    : assistantComposedMessages.length === 0
                        ? html`<p className="text-sm text-slate-400">还没有消息，先发送一条吧。</p>`
                        : assistantComposedMessages.map((message, index) => html`
                              <${ChatMessage} key=${message.id || `${message.role}-${index}`} message=${message} />
                          `)}
            </div>
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
                        placeholder="输入消息，使用 /技能名 可指定技能"
                        className="w-full rounded-xl border border-white/15 bg-ink-900/70 px-3 py-2 text-sm resize-none"
                        disabled=${assistantSending}
                    ></textarea>
                </div>
                <${Button} type="submit" disabled=${assistantSending || !assistantInput.trim()}>
                    ${assistantSending ? "发送中" : "发送"}
                <//>
            </form>
        </div>
    `;
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
