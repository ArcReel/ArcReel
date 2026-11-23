import { useRef } from "react";
import { Loader2, Upload } from "lucide-react";
import { TooltipIconButton } from "@/components/canvas/timeline/TooltipIconButton";

export const UPLOAD_IMAGE_ACCEPT = ".png,.jpg,.jpeg,.webp";
export const UPLOAD_VIDEO_ACCEPT = ".mp4,.mov,.m4v";

interface UploadIconButtonProps {
  accept: string;
  label: string;
  busy?: boolean;
  disabled?: boolean;
  onSelect: (file: File) => void;
}

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
          e.target.value = "";
          if (f) onSelect(f);
        }}
      />
      <TooltipIconButton label={label} disabled={busy || disabled} onClick={() => fileInputRef.current?.click()}>
        {busy ? <Loader2 className="animate-spin" aria-hidden="true" /> : <Upload aria-hidden="true" />}
      </TooltipIconButton>
    </>
  );
}
