import { StreamMarkdown } from "../StreamMarkdown";


interface TextBlockProps {
  text?: string;
  size?: "default" | "compact";
}

export function TextBlock({ text, size }: TextBlockProps) {
  if (!text) {
    return null;
  }

  return <StreamMarkdown content={text} size={size} />;
}
