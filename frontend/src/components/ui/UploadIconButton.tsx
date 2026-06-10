import { useRef } from "react";
import { Loader2, Upload } from "lucide-react";

/** 与后端 upload_finalize.py 的扩展名白名单保持一致 */
export const UPLOAD_IMAGE_ACCEPT = ".png,.jpg,.jpeg,.webp";
export const UPLOAD_VIDEO_ACCEPT = ".mp4,.mov,.m4v";

interface UploadIconButtonProps {
  accept: string;
  /** 同时作为 title 与 aria-label */
  label: string;
  /** 上传请求进行中：显示 spinner 并禁用 */
  busy?: boolean;
  disabled?: boolean;
  onSelect: (file: File) => void;
}

/** 卡片头部的图标式上传入口：隐藏 file input + 7x7 图标按钮。 */
export function UploadIconButton({ accept, label, busy, disabled, onSelect }: UploadIconButtonProps) {
  const fileInputRef = useRef<HTMLInputElement>(null);

  return (
    <>
      <input
        ref={fileInputRef}
        type="file"
        accept={accept}
        className="hidden"
        onChange={(e) => {
          const f = e.target.files?.[0];
          // 允许重复选择同一文件再次上传
          e.target.value = "";
          if (f) onSelect(f);
        }}
      />
      <button
        type="button"
        onClick={() => fileInputRef.current?.click()}
        disabled={busy || disabled}
        title={label}
        aria-label={label}
        className="focus-ring inline-flex h-7 w-7 items-center justify-center rounded-md transition-colors hover:bg-[oklch(1_0_0_/_0.05)] disabled:cursor-not-allowed disabled:opacity-50"
        style={{ color: "var(--color-text-3)" }}
      >
        {busy ? (
          <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
        ) : (
          <Upload className="h-3.5 w-3.5" aria-hidden="true" />
        )}
      </button>
    </>
  );
}
