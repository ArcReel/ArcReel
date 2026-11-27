import { useState } from "react";
import { useTranslation } from "react-i18next";
import { API } from "@/api";
import { RunSettings } from "@/components/shared/RunSettings";
import { Button } from "@/components/ui/button";
import { Dialog, DialogBody, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import type { GenerationOptions } from "@/stores/run-settings-store";
import { errMsg, voidCall } from "@/utils/async";

export function FreeImageDialog({ projectName, initialPrompt, onClose, onSubmitted }: {
  projectName: string;
  initialPrompt: string;
  onClose: () => void;
  onSubmitted: (task: { task_id: string; draft_id: string }) => void;
}) {
  const { t } = useTranslation("dashboard");
  const [prompt, setPrompt] = useState(initialPrompt);
  const [options, setOptions] = useState<GenerationOptions>({});
  const [sending, setSending] = useState(false);
  const [error, setError] = useState("");
  const submit = async () => {
    setSending(true);
    setError("");
    try {
      const { aspect_ratio, ...generation_options } = options;
      onSubmitted(await API.generateFreeImage(projectName, { prompt: prompt.trim(), aspect_ratio, generation_options }));
    } catch (cause) {
      setError(errMsg(cause, t("image_draft_failed")));
    } finally {
      setSending(false);
    }
  };

  return (
    <Dialog open onOpenChange={(open) => { if (!open && !sending) onClose(); }}>
      <DialogContent showCloseButton={!sending}>
        <DialogHeader><DialogTitle>{t("image_draft_generate")}</DialogTitle></DialogHeader>
        <DialogBody>
          <div className="flex flex-col gap-3">
            <Label htmlFor="free-image-prompt">{t("image_draft_prompt")}</Label>
            <Textarea id="free-image-prompt" value={prompt} onChange={(event) => setPrompt(event.target.value)} maxLength={10000} disabled={sending} />
            <RunSettings projectName={projectName} kind="image" draftOptions={options} onDraftOptionsChange={setOptions} disabled={sending} />
            {error && <p role="alert" className="text-destructive">{error}</p>}
          </div>
        </DialogBody>
        <DialogFooter>
          <Button variant="outline" disabled={sending} onClick={onClose}>{t("common:cancel")}</Button>
          <Button onClick={() => voidCall(submit())} disabled={sending || !prompt.trim()}>{sending ? t("loading") : t("image_draft_generate")}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
