import React from "react";
import htm from "htm";
import { cn } from "../utils.js";

const html = htm.bind(React.createElement);

export function Button({
    children,
    type = "button",
    variant = "default",
    size = "md",
    disabled = false,
    onClick,
    className = "",
}) {
    const variants = {
        default: "bg-neon-500 text-ink-950 hover:bg-neon-400",
        outline: "border border-white/20 text-slate-100 hover:border-neon-400/60 hover:text-neon-300",
        ghost: "text-slate-200 hover:bg-white/10",
        danger: "bg-red-500/85 text-white hover:bg-red-500",
    };

    const sizes = {
        sm: "h-8 px-3 text-xs rounded-lg",
        md: "h-10 px-4 text-sm rounded-xl",
        lg: "h-11 px-5 text-sm rounded-xl",
        icon: "h-10 w-10 rounded-xl",
    };

    return html`
        <button
            type=${type}
            disabled=${disabled}
            onClick=${onClick}
            className=${cn(
                "inline-flex items-center justify-center gap-2 font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed",
                variants[variant] || variants.default,
                sizes[size] || sizes.md,
                className
            )}
        >
            ${children}
        </button>
    `;
}

export function Badge({ children, className = "" }) {
    return html`
        <span className=${cn("inline-flex items-center px-2.5 py-1 text-xs rounded-full", className)}>
            ${children}
        </span>
    `;
}

export function Card({ children, className = "" }) {
    return html`
        <section className=${cn("app-panel rounded-2xl p-5", className)}>
            ${children}
        </section>
    `;
}

export function EmptyState({ title, description, action }) {
    return html`
        <div className="rounded-2xl border border-dashed border-white/20 bg-white/5 p-10 text-center">
            <p className="text-lg font-medium text-slate-100">${title}</p>
            <p className="mt-2 text-sm text-slate-400">${description}</p>
            ${action ? html`<div className="mt-5">${action}</div>` : null}
        </div>
    `;
}
