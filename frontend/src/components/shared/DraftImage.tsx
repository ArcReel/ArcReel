import { useEffect, useState } from "react";
import { API } from "@/api";

export function DraftImage({ projectName, draftId, alt, className }: {
  projectName: string; draftId: string; alt: string; className?: string;
}) {
  const [src, setSrc] = useState<string>();
  useEffect(() => {
    const controller = new AbortController();
    let url: string | undefined;
    void API.getFreeImageDraftImage(projectName, draftId, controller.signal).then((blob) => {
      if (controller.signal.aborted) return;
      url = URL.createObjectURL(blob);
      setSrc(url);
    }, () => {});
    return () => { controller.abort(); if (url) URL.revokeObjectURL(url); };
  }, [projectName, draftId]);
  return <img src={src} alt={alt} className={className} />;
}
