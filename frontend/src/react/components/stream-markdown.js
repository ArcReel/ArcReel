import React, { useEffect, useState } from "react";
import htm from "htm";

const html = htm.bind(React.createElement);

let streamdownPromise = null;

async function loadStreamdownComponent() {
    if (streamdownPromise) {
        return streamdownPromise;
    }

    streamdownPromise = import("streamdown")
        .then((mod) => mod.Streamdown || mod.default || null)
        .catch((error) => {
            console.warn("Failed to load Streamdown:", error);
            return null;
        });

    return streamdownPromise;
}

export function StreamMarkdown({ content }) {
    const [StreamdownComponent, setStreamdownComponent] = useState(null);

    useEffect(() => {
        let mounted = true;

        loadStreamdownComponent().then((component) => {
            if (!mounted || !component) return;
            setStreamdownComponent(() => component);
        });

        return () => {
            mounted = false;
        };
    }, []);

    if (!StreamdownComponent) {
        return html`<div className="whitespace-pre-wrap">${content || ""}</div>`;
    }

    return html`
        <${StreamdownComponent} className="markdown-body text-sm leading-6" parseIncompleteMarkdown=${true}>
            ${String(content || "")}
        <//>
    `;
}
