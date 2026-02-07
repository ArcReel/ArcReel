import React from "react";
import htm from "htm";

import { Button } from "./primitives.js";

const html = htm.bind(React.createElement);

export function AssistantDeleteSessionDialog({
    open,
    sessionTitle,
    submitting,
    onClose,
    onConfirm,
}) {
    if (!open) {
        return null;
    }

    const displayTitle = sessionTitle?.trim() || "未命名会话";

    return html`
        <div className="fixed inset-0 z-[66] bg-black/50 backdrop-blur-sm flex items-center justify-center px-4">
            <form onSubmit=${onConfirm} className="w-full max-w-md rounded-2xl app-panel-strong p-5 space-y-4">
                <div className="space-y-1">
                    <h2 className="text-lg font-semibold text-red-200">删除会话</h2>
                    <p className="text-sm text-slate-300">
                        确认删除「${displayTitle}」？删除后不可恢复。
                    </p>
                </div>

                <div className="pt-2 flex justify-end gap-2">
                    <${Button} variant="ghost" onClick=${onClose} disabled=${submitting}>取消<//>
                    <${Button} type="submit" variant="danger" disabled=${submitting}>
                        ${submitting ? "删除中..." : "确认删除"}
                    <//>
                </div>
            </form>
        </div>
    `;
}
