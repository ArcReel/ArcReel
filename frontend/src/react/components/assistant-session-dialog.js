import React from "react";
import htm from "htm";

import { Button } from "./primitives.js";

const html = htm.bind(React.createElement);

export function AssistantSessionDialog({
    open,
    mode,
    title,
    setTitle,
    submitting,
    onClose,
    onSubmit,
}) {
    if (!open) {
        return null;
    }

    const isCreate = mode === "create";
    const dialogTitle = isCreate ? "新建会话" : "重命名会话";
    const placeholder = isCreate ? "可选，会默认使用系统标题" : "请输入会话标题";
    const submitText = isCreate ? "创建会话" : "保存标题";

    return html`
        <div className="fixed inset-0 z-[65] bg-black/50 backdrop-blur-xs flex items-center justify-center px-4">
            <form onSubmit=${onSubmit} className="w-full max-w-md rounded-2xl app-panel-strong p-5 space-y-4">
                <div className="flex items-center justify-between">
                    <h2 className="text-lg font-semibold">${dialogTitle}</h2>
                    <button
                        type="button"
                        onClick=${onClose}
                        disabled=${submitting}
                        className="h-8 w-8 rounded-lg hover:bg-white/10 disabled:opacity-50"
                    >
                        ×
                    </button>
                </div>

                <label className="text-sm text-slate-300 flex flex-col gap-2">
                    会话标题
                    <input
                        value=${title}
                        onChange=${(event) => setTitle(event.target.value)}
                        className="h-10 rounded-xl border border-white/15 bg-ink-900/70 px-3"
                        placeholder=${placeholder}
                        autoFocus
                    />
                </label>

                <div className="pt-2 flex justify-end gap-2">
                    <${Button} variant="ghost" onClick=${onClose} disabled=${submitting}>取消<//>
                    <${Button} type="submit" disabled=${submitting}>
                        ${submitting ? "处理中..." : submitText}
                    <//>
                </div>
            </form>
        </div>
    `;
}
