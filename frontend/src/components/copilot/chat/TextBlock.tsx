import { StreamMarkdown } from "../StreamMarkdown";

// ---------------------------------------------------------------------------
// TextBlock – renders plain text / markdown content via StreamMarkdown.
// ---------------------------------------------------------------------------

interface TextBlockProps {
  text?: string;
}

export function TextBlock({ text }: TextBlockProps) {
  if (!text) {
    return null;
  }

  return <StreamMarkdown content={text} />;
}
