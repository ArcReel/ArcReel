import { useTranslation } from "react-i18next";
import { cn } from "cn";
import { Dialog, DialogBody, DialogContent, DialogHeader, DialogTitle, DialogTrigger } from "@/components/ui/dialog";

// ---------------------------------------------------------------------------
// ChatImage: image attachment in a message; open a dialog to view the original.
// ---------------------------------------------------------------------------

interface ChatImageProps {
  src: string;
  /** Position within its message, starting at 1, for the accessible name. */
  index: number;
  /** thumbnail: fixed-height thumbnail in user bubbles; inline: preserve aspect ratio in Agent message bodies. */
  variant?: "thumbnail" | "inline";
}

export function ChatImage({ src, index, variant = "thumbnail" }: ChatImageProps) {
  const { t } = useTranslation("dashboard");
  const alt = t("chat_image_attachment", { index });

  return (
    <Dialog>
      <DialogTrigger
        render={
          <button
            type="button"
            aria-label={t("chat_image_enlarge", { index })}
            className="max-w-full shrink-0 overflow-hidden rounded-md border border-border outline-none transition-colors duration-fast hover:border-ring focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50"
          />
        }
      >
        <img
          src={src}
          alt={alt}
          className={cn(
            "block max-w-full",
            variant === "thumbnail" ? "h-20 w-auto object-cover" : "max-h-64 w-auto object-contain",
          )}
        />
      </DialogTrigger>
      <DialogContent size="xl">
        <DialogHeader>
          <DialogTitle>{alt}</DialogTitle>
        </DialogHeader>
        {/* Only this area scrolls for very tall originals; it has no focusable children, so keyboard scrolling requires focusing the area itself */}
        <DialogBody tabIndex={0} role="region" aria-label={alt}>
          <img src={src} alt={alt} className="mx-auto block h-auto max-w-full rounded-md" />
        </DialogBody>
      </DialogContent>
    </Dialog>
  );
}
