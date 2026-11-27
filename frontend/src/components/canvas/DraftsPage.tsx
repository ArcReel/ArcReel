import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Images } from "lucide-react";
import { DraftImage } from "@/components/shared/DraftImage";
import { useDraftCountStore } from "@/stores/draft-count-store";
import { API } from "@/api";
import { Button } from "@/components/ui/button";
import { Dialog, DialogBody, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { refreshAfterWrite } from "@/components/canvas/shared/refreshAfterWrite";
import { useProjectsStore } from "@/stores/projects-store";
import type { FreeImageDraft } from "@/types/free-image-draft";
import { errMsg, voidCall } from "@/utils/async";

const TYPES = ["character", "scene", "prop"] as const;
type AssetType = (typeof TYPES)[number];

export function DraftsPage({ projectName }: { projectName: string }) {
  const { t } = useTranslation("dashboard");
  const [items, setItems] = useState<FreeImageDraft[]>();
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [active, setActive] = useState<{ draft: FreeImageDraft; mode: "preview" | "bind" | "delete" }>();
  const [reload, setReload] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    void API.listFreeImageDrafts(projectName, controller.signal).then(({ items }) => {
      if (!controller.signal.aborted) {
        setItems(items);
        useDraftCountStore.getState().setCount(projectName, items.length);
        setError("");
      }
    }, (cause: unknown) => {
      if (!controller.signal.aborted) setError(errMsg(cause, t("image_draft_failed")));
    });
    return () => controller.abort();
  }, [projectName, reload, t]);
  return (
    <main className="relative flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto p-5">
      <header className="flex flex-wrap justify-between gap-2">
        <div><h1 className="text-lg font-semibold">{t("image_draft_library")}</h1><p className="text-sm text-muted-foreground">{t("image_draft_intro")}</p></div>
        <Button variant="outline" onClick={() => setReload((value) => value + 1)}>{t("image_draft_refresh")}</Button>
      </header>
      {error && <p role="alert" className="text-destructive">{error}</p>}
      {notice && <p role="status" className="text-sm text-muted-foreground">{notice}</p>}
      {!items ? (!error && <p role="status">{t("loading")}</p>) : items.length === 0 ? (
        <div className="grid justify-items-center gap-2 rounded-lg border border-dashed py-12 text-muted-foreground"><Images aria-hidden /><p>{t("image_draft_empty")}</p></div>
      ) : (
        <ul className="grid grid-cols-[repeat(auto-fill,minmax(min(100%,13rem),1fr))] gap-4">
          {items.map((draft) => (
            <li key={draft.id} className="overflow-hidden rounded-lg border bg-card">
              <DraftImage projectName={projectName} draftId={draft.id} alt={draft.prompt} className="aspect-square w-full bg-muted/30 object-contain" />
              <div className="flex flex-col gap-2 p-3">
                <p className="line-clamp-2 break-words text-sm">{draft.prompt}</p>
                <p className="truncate text-xs text-muted-foreground">{[draft.model, draft.aspect_ratio, draft.resolution].filter(Boolean).join(" · ")}</p>
                <div className="flex flex-wrap gap-1">
                  <Button size="sm" variant="outline" onClick={() => setActive({ draft, mode: "preview" })}>{t("image_draft_preview")}</Button>
                  <Button size="sm" variant="outline" onClick={() => setActive({ draft, mode: "bind" })}>{t("image_draft_bind")}</Button>
                  <Button size="sm" variant="ghost" onClick={() => setActive({ draft, mode: "delete" })}>{t("image_draft_delete")}</Button>
                </div>
              </div>
            </li>
          ))}
        </ul>
      )}
      {active && <DraftAction projectName={projectName} {...active} onClose={() => setActive(undefined)} onDone={() => {
        if (active.mode === "delete") {
          const remaining = items?.filter((item) => item.id !== active.draft.id);
          setItems(remaining);
          if (remaining) useDraftCountStore.getState().setCount(projectName, remaining.length);
          useDraftCountStore.getState().invalidate(projectName);
        }
        setNotice(t(active.mode === "delete" ? "image_draft_deleted" : "image_draft_bound"));
        setActive(undefined);
      }} />}
    </main>
  );
}

function DraftAction({ projectName, draft, mode, onClose, onDone }: {
  projectName: string; draft: FreeImageDraft; mode: "preview" | "bind" | "delete"; onClose: () => void; onDone: () => void;
}) {
  const { t } = useTranslation("dashboard");
  const project = useProjectsStore((s) => s.currentProjectData);
  const [type, setType] = useState<AssetType>("character");
  const [name, setName] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const typeItems = TYPES.map((value) => ({ value, label: t(`workspace_nav_${value}s`) }));
  const bucket = project?.[`${type}s`] ?? {};
  const names = Object.keys(bucket);
  const submit = async () => {
    setBusy(true);
    setError("");
    try {
      if (mode === "delete") await API.deleteFreeImageDraft(projectName, draft.id);
      else if (name) {
        await API.bindFreeImageDraft(projectName, draft.id, type, name);
        await refreshAfterWrite(projectName, t);
      }
      onDone();
    } catch (cause) {
      setError(errMsg(cause, t("image_draft_failed")));
    } finally { setBusy(false); }
  };
  return (
    <Dialog open onOpenChange={(open) => { if (!open && !busy) onClose(); }}>
      <DialogContent size={mode === "preview" ? "lg" : "sm"} showCloseButton={!busy}>
        <DialogHeader><DialogTitle>{t(`image_draft_${mode}`)}</DialogTitle></DialogHeader>
        <DialogBody>
          {mode === "preview" && <><DraftImage projectName={projectName} draftId={draft.id} alt={draft.prompt} className="mx-auto max-h-96 max-w-full object-contain" /><p className="mt-3 whitespace-pre-wrap break-words">{draft.prompt}</p></>}
          {mode === "delete" && <p>{t("image_draft_delete_confirm")}</p>}
          {mode === "bind" && <div className="flex flex-col gap-3">
            <Select items={typeItems} value={type} disabled={busy} onValueChange={(value) => { if (value) { setType(value); setName(null); } }}>
              <SelectTrigger aria-label={t("image_draft_asset_type")}><SelectValue /></SelectTrigger>
              <SelectContent><SelectGroup>{typeItems.map(({ value, label }) => <SelectItem key={value} value={value}>{label}</SelectItem>)}</SelectGroup></SelectContent>
            </Select>
            <Select items={names.map((value) => ({ value, label: value }))} value={name} disabled={busy || !names.length} onValueChange={setName}>
              <SelectTrigger aria-label={t("image_draft_asset_name")}><SelectValue placeholder={t("image_draft_asset_name")} /></SelectTrigger>
              <SelectContent><SelectGroup>{names.map((value) => <SelectItem key={value} value={value}>{value}</SelectItem>)}</SelectGroup></SelectContent>
            </Select>
            <p className="text-muted-foreground">{t(names.length ? "image_draft_replace_hint" : "image_draft_no_assets")}</p>
          </div>}
          {error && <p role="alert" className="mt-2 text-destructive">{error}</p>}
        </DialogBody>
        {mode !== "preview" && <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={busy}>{t("common:cancel")}</Button>
          <Button variant={mode === "delete" ? "destructive" : "default"} onClick={() => voidCall(submit())} disabled={busy || (mode === "bind" && !name)}>{t(`image_draft_${mode}`)}</Button>
        </DialogFooter>}
      </DialogContent>
    </Dialog>
  );
}
