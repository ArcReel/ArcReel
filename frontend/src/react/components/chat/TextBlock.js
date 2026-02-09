import React from "react";
import htm from "htm";
import { StreamMarkdown } from "../stream-markdown.js";

const html = htm.bind(React.createElement);

export function TextBlock({ text }) {
    if (!text) {
        return null;
    }

    return html`<${StreamMarkdown} content=${text} />`;
}
