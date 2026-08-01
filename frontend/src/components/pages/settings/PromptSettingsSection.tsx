import { useCallback, useEffect, useState } from "react";
import { Loader2, RotateCcw } from "lucide-react";
import { useTranslation } from "react-i18next";
import { API } from "@/api";
import { useWarnUnsaved } from "@/hooks/useWarnUnsaved";
import { useAppStore } from "@/stores/app-store";
import type { SystemConfigPatch, SystemConfigSettings } from "@/types/system";
import { errMsg } from "@/utils/async";
import { ACCENT_BTN_CLS, ACCENT_BUTTON_STYLE, CARD_STYLE } from "@/components/ui/darkroom-tokens";

type PromptSettingKey = "asset_prompt_character" | "asset_prompt_scene" | "asset_prompt_prop";
type PromptKind = "character" | "scene" | "prop";

interface PromptDefaults {
  character: string;
  scene: string;
  prop: string;
}

const PROMPT_FIELDS: readonly [PromptKind, string, PromptSettingKey][] = [
  ["character", "asset_prompt_character_label", "asset_prompt_character"],
  ["scene", "asset_prompt_scene_label", "asset_prompt_scene"],
  ["prop", "asset_prompt_prop_label", "asset_prompt_prop"],
];

const EMPTY_DEFAULTS: PromptDefaults = { character: "", scene: "", prop: "" };

export function PromptSettingsSection() {
  const { t } = useTranslation("dashboard");
  const [settings, setSettings] = useState<SystemConfigSettings | null>(null);
  const [defaults, setDefaults] = useState<PromptDefaults>(EMPTY_DEFAULTS);
  const [draft, setDraft] = useState<SystemConfigPatch>({});
  const [saving, setSaving] = useState(false);

  const isDirty = Object.keys(draft).length > 0;
  useWarnUnsaved(isDirty);

  const fetchConfig = useCallback(async () => {
    const response = await API.getSystemConfig();
    setSettings(response.settings);
    setDefaults(response.options.asset_prompt_defaults ?? EMPTY_DEFAULTS);
    setDraft({});
  }, []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- async fetch completion initializes server-backed form state
    void fetchConfig();
  }, [fetchConfig]);

  const handleSave = useCallback(async () => {
    if (!isDirty) return;
    setSaving(true);
    try {
      await API.updateSystemConfig(draft);
      await fetchConfig();
      useAppStore.getState().pushToast(t("asset_prompts_saved"), "success");
    } catch (error) {
      useAppStore.getState().pushToast(t("save_failed", { message: errMsg(error) }), "error");
    } finally {
      setSaving(false);
    }
  }, [draft, fetchConfig, isDirty, t]);

  if (!settings) {
    return (
      <div className="flex items-center gap-2 px-1 py-12 text-text-3">
        <Loader2 className="h-3.5 w-3.5 motion-safe:animate-spin text-accent-2" aria-hidden />
        <span className="font-mono text-[11px] uppercase tracking-[0.14em]">
          {t("common:loading")}
        </span>
      </div>
    );
  }

  const values: Record<PromptKind, string> = {
    character: (draft.asset_prompt_character ?? settings.asset_prompt_character) || defaults.character,
    scene: (draft.asset_prompt_scene ?? settings.asset_prompt_scene) || defaults.scene,
    prop: (draft.asset_prompt_prop ?? settings.asset_prompt_prop) || defaults.prop,
  };

  return (
    <div className="space-y-7">
      <div>
        <div className="font-mono text-[10px] font-bold uppercase tracking-[0.18em] text-accent-2">
          {t("asset_prompts_kicker")}
        </div>
        <h3
          className="font-editorial mt-1"
          style={{
            fontWeight: 400,
            fontSize: 22,
            lineHeight: 1.1,
            letterSpacing: "-0.012em",
            color: "var(--color-text)",
          }}
        >
          {t("asset_prompts_title")}
        </h3>
        <p className="mt-1.5 text-[12.5px] leading-[1.6] text-text-3">
          {t("asset_prompts_desc")}
        </p>
      </div>

      <div className="rounded-[10px] border border-hairline p-5" style={CARD_STYLE}>
        <div className="space-y-5">
          {PROMPT_FIELDS.map(([kind, labelKey, settingKey]) => (
            <div key={kind}>
              <div className="mb-1.5 flex items-center justify-between gap-3">
                <label
                  htmlFor={`asset-prompt-${kind}`}
                  className="font-mono text-[10px] font-bold uppercase tracking-[0.14em] text-text-4"
                >
                  {t(labelKey)}
                </label>
                <button
                  type="button"
                  onClick={() => setDraft((previous) => ({ ...previous, [settingKey]: "" }))}
                  className="inline-flex items-center gap-1 text-[10.5px] text-text-4 transition-colors hover:text-text-2"
                >
                  <RotateCcw className="h-3 w-3" />
                  {t("asset_prompt_restore_default")}
                </button>
              </div>
              <textarea
                id={`asset-prompt-${kind}`}
                rows={7}
                value={values[kind]}
                onChange={(event) =>
                  setDraft((previous) => ({ ...previous, [settingKey]: event.target.value }))
                }
                className="w-full resize-y rounded-[8px] border border-hairline bg-bg-grad-a/55 px-3 py-2.5 text-[12.5px] leading-[1.6] text-text focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
              />
              <p className="mt-1 text-[11px] text-text-4">{t("asset_prompt_merge_hint")}</p>
            </div>
          ))}
        </div>
      </div>

      {isDirty && (
        <div className="flex gap-2 pt-1">
          <button
            type="button"
            onClick={() => void handleSave()}
            disabled={saving}
            className={ACCENT_BTN_CLS}
            style={ACCENT_BUTTON_STYLE}
          >
            {saving ? <Loader2 className="h-3.5 w-3.5 motion-safe:animate-spin" aria-hidden /> : null}
            {saving ? t("common:saving") : t("common:save")}
          </button>
          <button
            type="button"
            onClick={() => setDraft({})}
            className="rounded-[8px] border border-hairline bg-bg-grad-a/55 px-4 py-2 text-[12.5px] text-text-2 transition-colors hover:border-hairline-strong hover:bg-bg-grad-a hover:text-text focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
          >
            {t("common:reset")}
          </button>
        </div>
      )}
    </div>
  );
}
