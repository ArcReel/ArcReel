import React from "react";
import htm from "htm";

const html = htm.bind(React.createElement);

export function AppToast({ toast }) {
    if (!toast) {
        return null;
    }

    const className =
        toast.tone === "error"
            ? "border-red-400/40 bg-red-500/15 text-red-200"
            : "border-neon-400/40 bg-neon-500/15 text-neon-200";

    return html`
        <div className=${`fixed right-5 top-5 z-[70] toast-enter rounded-xl border px-4 py-3 text-sm ${className}`}>
            ${toast.text}
        </div>
    `;
}
