import { useTranslation } from "react-i18next";
import { cn } from "cn";
import { Dialog, DialogBody, DialogContent, DialogHeader, DialogTitle, DialogTrigger } from "@/components/ui/dialog";


interface ChatImageProps {
  src: string;
  index: number;
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
        <DialogBody tabIndex={0} role="region" aria-label={alt}>
          <img src={src} alt={alt} className="mx-auto block h-auto max-w-full rounded-md" />
        </DialogBody>
      </DialogContent>
    </Dialog>
  );
}
