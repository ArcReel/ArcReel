import { useTranslation } from "react-i18next";
import { cn } from "cn";
import { Dialog, DialogBody, DialogContent, DialogHeader, DialogTitle, DialogTrigger } from "@/components/ui/dialog";

// ---------------------------------------------------------------------------
// ChatImage — 消息里的图片附件：点开在对话框里看原图。
// ---------------------------------------------------------------------------

interface ChatImageProps {
  src: string;
  /** 在所属消息里的序号（从 1 开始），用于可访问名称。 */
  index: number;
  /** thumbnail：用户气泡里的固定高度缩略图；inline：Agent 正文里按原比例显示。 */
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
        {/* 原图超高时只有这里滚动，内部没有可聚焦元素，键盘靠它自身聚焦后滚动 */}
        <DialogBody tabIndex={0} role="region" aria-label={alt}>
          <img src={src} alt={alt} className="mx-auto block h-auto max-w-full rounded-md" />
        </DialogBody>
      </DialogContent>
    </Dialog>
  );
}
