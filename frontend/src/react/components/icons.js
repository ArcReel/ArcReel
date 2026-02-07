import React from "react";
import htm from "htm";

const html = htm.bind(React.createElement);

export function IconProject() {
    return html`
        <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.8" d="M3 7h18M3 12h18M3 17h18" />
        </svg>
    `;
}

export function IconAssistant() {
    return html`
        <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.8" d="M8 10h.01M12 10h.01M16 10h.01M21 12c0 4.418-4.03 8-9 8a9.94 9.94 0 01-4.255-.949L3 20l1.5-4.5A7.57 7.57 0 013 12c0-4.418 4.03-8 9-8s9 3.582 9 8z" />
        </svg>
    `;
}

export function IconUsage() {
    return html`
        <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.8" d="M7 20V10m5 10V4m5 16v-6" />
        </svg>
    `;
}

export function IconSpark() {
    return html`
        <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.8" d="M12 2l2.5 6.5L21 11l-6.5 2.5L12 20l-2.5-6.5L3 11l6.5-2.5L12 2z" />
        </svg>
    `;
}
