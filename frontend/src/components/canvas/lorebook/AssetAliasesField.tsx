import { useEffect, useId, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Loader2, Plus, X } from "lucide-react";
import { API } from "@/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { useAppStore } from "@/stores/app-store";
import { errMsg } from "@/utils/async";
import { refreshAfterWrite } from "@/components/canvas/shared/refreshAfterWrite";
import { rejectIfAssetBusy } from "./assetBusyGuard";
import { useTrackWrite } from "./useAssetWrites";

export type AliasAssetType = "character" | "scene" | "prop";

interface AssetAliasesFieldProps {
  projectName: string;
  name: string;
  assetType: AliasAssetType;
  aliases: string[];
  /** Read-only onboarding demos list aliases without add/remove entry points. */
  readOnly?: boolean;
  /** Other detail writes are occupied; disable alias additions/removals together. */
  busy?: boolean;
}

const UPDATE: Record<AliasAssetType, (project: string, name: string, updates: Record<string, unknown>) => Promise<unknown>> = {
  character: (project, name, updates) => API.updateCharacter(project, name, updates),
  scene: (project, name, updates) => API.updateProjectScene(project, name, updates),
  prop: (project, name, updates) => API.updateProjectProp(project, name, updates),
};

/**
 * Detail aliases: add/remove execute immediately, outside unsaved edits. Adding explicitly submits the add-alias popover form;
 * removal notices offer undo. Aliases help AI planning recognize identities, never reference matching; backend name comparison
 * trims/deduplicates and removes aliases equivalent to asset names.
 */
export function AssetAliasesField({
  projectName,
  name,
  assetType,
  aliases,
  readOnly = false,
  busy = false,
}: AssetAliasesFieldProps) {
  const { t } = useTranslation(["assets", "common"]);
  const [input, setInput] = useState("");
  const [adding, setAdding] = useState(false);
  const [saving, setSaving] = useState(false);
  const inputId = useId();
  const inputRef = useRef<HTMLInputElement>(null);
  const track = useTrackWrite();
  // Notice-triggered undo may follow later refreshes: add back against aliases current at that moment.
  const latest = useRef({ aliases, name, busy, saving });
  useEffect(() => {
    latest.current = { aliases, name, busy, saving };
  }, [aliases, name, busy, saving]);

  /**
   * Save the whole alias list. `stale` means saved without current-data synchronization (refresh failed with feedback, or project changed);
   * remaining old lists cannot reliably support subsequent actions such as undo.
   */
  const save = async (target: string, next: string[]): Promise<"saved" | "stale" | "failed"> => {
    if (latest.current.busy || latest.current.saving || rejectIfAssetBusy(assetType, projectName, target, t, "assets:gallery_busy_hint")) return "failed";
    setSaving(true);
    try {
      const refreshed = await track(
        UPDATE[assetType](projectName, target, { aliases: next }).then(() => refreshAfterWrite(projectName, t)),
      );
      // Undo needs truly refreshed lists for reliability; cancelled means project navigation.
      return refreshed === "success" ? "saved" : "stale";
    } catch (err) {
      useAppStore.getState().pushToast(t("assets:aliases_save_failed", { message: errMsg(err) }), "error");
      return "failed";
    } finally {
      setSaving(false);
    }
  };

  const add = async () => {
    const alias = input.trim();
    if (!alias || saving) return;
    if ((await save(name, [...aliases, alias])) !== "failed") {
      setInput("");
      setAdding(false);
    }
  };

  const remove = async (alias: string) => {
    if ((await save(name, aliases.filter((item) => item !== alias))) !== "saved") return;
    useAppStore.getState().pushToast(t("assets:aliases_removed", { alias }), "info", {
      action: {
        label: t("common:undo"),
        onClick: () => {
          const { aliases: current, name: target } = latest.current;
          if (!current.includes(alias)) void save(target, [...current, alias]);
        },
      },
    });
  };

  // Callers omit the whole block when read-only without aliases.
  if (readOnly) {
    return (
      <ul className="flex flex-wrap gap-1.5">
        {aliases.map((alias) => (
          <li key={alias} className="rounded-md border border-border px-2 py-0.5 text-sm">
            {alias}
          </li>
        ))}
      </ul>
    );
  }

  const locked = saving || busy;

  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex flex-wrap items-center gap-1.5">
        {aliases.length > 0 && (
          <ul className="contents">
            {aliases.map((alias) => (
              <li key={alias} className="inline-flex h-7 items-center gap-0.5 rounded-md border border-border pr-0.5 pl-2 text-sm">
                {alias}
                <Button
                  variant="ghost"
                  size="icon-xs"
                  onClick={() => void remove(alias)}
                  disabled={locked}
                  aria-label={t("assets:aliases_remove", { alias })}
                >
                  <X aria-hidden />
                </Button>
              </li>
            ))}
          </ul>
        )}
        <Popover
          open={adding}
          onOpenChange={(next) => {
            // Ignore closing during submission to preserve input while requests run.
            if (!next && saving) return;
            if (next && rejectIfAssetBusy(assetType, projectName, name, t, "assets:gallery_busy_hint")) return;
            setAdding(next);
            if (!next) setInput("");
          }}
        >
          <PopoverTrigger render={<Button variant="outline" size="sm" disabled={locked} />}>
            <Plus aria-hidden data-icon="inline-start" />
            {t("assets:aliases_add")}
          </PopoverTrigger>
          <PopoverContent align="start" initialFocus={inputRef}>
            <form
              className="flex flex-col gap-2"
              onSubmit={(e) => {
                e.preventDefault();
                void add();
              }}
            >
              <label htmlFor={inputId} className="text-sm font-medium">
                {t("assets:aliases_label")}
              </label>
              <div className="flex items-center gap-1.5">
                <Input
                  ref={inputRef}
                  id={inputId}
                  value={input}
                  onChange={(e) => setInput(e.target.value)}
                  disabled={saving}
                  className="min-w-0 flex-1"
                />
                <Button type="submit" size="sm" disabled={saving || input.trim().length === 0}>
                  {saving ? <Loader2 aria-hidden data-icon="inline-start" className="animate-spin" /> : null}
                  {t("common:add")}
                </Button>
              </div>
            </form>
          </PopoverContent>
        </Popover>
      </div>
      <p className="text-xs text-muted-foreground">{t("assets:aliases_hint")}</p>
    </div>
  );
}
