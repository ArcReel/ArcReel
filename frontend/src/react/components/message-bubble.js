import React from "react";
import htm from "htm";
import { cn, getRoleLabel } from "../utils.js";
import { StreamMarkdown } from "./stream-markdown.js";

const html = htm.bind(React.createElement);

export function MessageBubble({ message }) {
    const role = message.role || "assistant";
    const isAssistant = role === "assistant";

    const bubbleClass = {
        assistant: "bg-white/8 border border-white/10 mr-3",
        user: "bg-neon-500/20 border border-neon-400/25 ml-8",
        tool: "bg-amberx-500/10 border border-amberx-400/25 mr-5 ml-5",
    }[role] || "bg-white/8 border border-white/10";

    return html`
        <article className=${cn("rounded-xl px-3 py-2", bubbleClass)}>
            <div className="text-[11px] uppercase tracking-wide text-slate-400">${getRoleLabel(role)}</div>
            <div className="mt-1 text-sm text-slate-100 leading-6">
                ${isAssistant
                    ? html`<${StreamMarkdown} content=${message.content || ""} />`
                    : html`<div className="whitespace-pre-wrap">${message.content || ""}</div>`}
            </div>
            ${Array.isArray(message.streamStatus) && message.streamStatus.length > 0
                ? html`
                      <div className="mt-2 space-y-1 text-xs text-slate-300">
                          ${message.streamStatus.map(
                              (line, index) => html`<div key=${`${message.id || "stream"}-${index}`}>${line}</div>`
                          )}
                      </div>
                  `
                : null}
        </article>
    `;
}
