import { useId, useState, type CSSProperties } from "react";
import { useTranslation } from "react-i18next";
import { Lightbulb, Pencil } from "lucide-react";
import { API } from "@/api";
import { DurationTierPicker } from "@/components/shared/DurationTierPicker";
import { useAppStore } from "@/stores/app-store";
import { errMsg } from "@/utils/async";

const CARD_STYLE: CSSProperties = {
  border: "1px solid color-mix(in oklab, var(--border) 50%, transparent)",
  background: "linear-gradient(180deg, oklch(0.22 0.012 265 / 0.55), oklch(0.19 0.010 265 / 0.40))",
  boxShadow: "inset 0 1px 0 oklch(1 0 0 / 0.04), 0 8px 24px -10px oklch(0 0 0 / 0.5)",
};
const FIELD_STYLE: CSSProperties = {
  background: "linear-gradient(180deg, oklch(0.20 0.011 265 / 0.6), oklch(0.18 0.010 265 / 0.45))",
  border: "1px solid var(--border)",
  color: "var(--foreground)",
  boxShadow: "inset 0 1px 2px oklch(0 0 0 / 0.2)",
};

interface Props {
  projectName: string;
  brief: string;
  targetDuration: number | undefined;
  readOnly?: boolean;
  /** 保存成功后刷新项目。 */
  onSaved: () => Promise<void>;
}

/**
 * 广告/短片项目概览上常驻的创作灵感区：AI 生成脚本按这里的创作灵感与目标总时长规划。
 * 卖点不在这里，按商品逐个填写在商品页。
 */
export function AdBriefCard({ projectName, brief, targetDuration, readOnly = false, onSaved }: Props) {
  const { t } = useTranslation(["dashboard", "common"]);
  const briefFieldId = useId();
  const [editing, setEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [draftBrief, setDraftBrief] = useState("");
  const [seconds, setSeconds] = useState<number | null>(null);

  const enterEdit = () => {
    setDraftBrief(brief);
    setSeconds(targetDuration ?? null);
    setEditing(true);
  };

  const invalid = seconds === null;

  const save = async () => {
    if (saving || seconds === null) return;
    setSaving(true);
    try {
      await API.updateProject(projectName, { brief: draftBrief.trim(), target_duration: seconds });
      await onSaved();
      setEditing(false);
      useAppStore.getState().pushToast(t("ad_brief_saved"), "success");
    } catch (err) {
      useAppStore.getState().pushToast(t("ad_brief_save_failed", { message: errMsg(err) }), "error");
    } finally {
      setSaving(false);
    }
  };

  return (
    <section className="relative overflow-hidden rounded-2xl p-5" style={CARD_STYLE} aria-labelledby={briefFieldId + "-title"}>
      <div className="mb-3 flex items-center gap-2.5">
        <Lightbulb className="h-3.5 w-3.5" style={{ color: "var(--primary)" }} aria-hidden="true" />
        <h2
          id={briefFieldId + "-title"}
          className="text-[10.5px] font-bold uppercase"
          style={{ color: "var(--muted-foreground)", letterSpacing: "1.0px" }}
        >
          {t("ad_init_brief_label")}
        </h2>
        <div className="flex-1" />
        {!editing && !readOnly && (
          <button
            type="button"
            onClick={enterEdit}
            className="focus-ring inline-flex items-center gap-1 rounded-md px-2 py-1 text-[11px] text-muted-foreground transition-colors hover:bg-[oklch(1_0_0_/_0.05)] hover:text-foreground"
          >
            <Pencil className="h-3 w-3" aria-hidden="true" />
            <span>{t("ad_brief_edit")}</span>
          </button>
        )}
      </div>

      {editing ? (
        <div className="space-y-4">
          <textarea
            id={briefFieldId}
            aria-label={t("ad_init_brief_label")}
            value={draftBrief}
            onChange={(event) => setDraftBrief(event.target.value)}
            disabled={saving}
            rows={4}
            placeholder={t("ad_init_brief_placeholder")}
            className="focus-ring w-full resize-y rounded-lg px-3 py-2 text-[13px] leading-[1.6] outline-none"
            style={FIELD_STYLE}
          />
          <DurationTierPicker value={seconds} onChange={setSeconds} disabled={saving} />
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => void save()}
              disabled={saving || invalid}
              className="arc-btn-primary focus-ring rounded-md px-3 py-1.5 text-[12px] font-medium disabled:cursor-not-allowed disabled:opacity-50"
            >
              {saving ? t("common:saving") : t("common:save")}
            </button>
            <button
              type="button"
              onClick={() => setEditing(false)}
              disabled={saving}
              className="focus-ring rounded-md px-3 py-1.5 text-[12px] text-muted-foreground transition-colors hover:text-foreground disabled:opacity-50"
            >
              {t("common:cancel")}
            </button>
          </div>
        </div>
      ) : (
        <>
          <p
            className="whitespace-pre-line text-[13px] leading-[1.7]"
            style={{ color: brief.trim() ? "var(--subtle-foreground)" : "var(--muted-foreground)" }}
          >
            {brief.trim() || t("ad_brief_empty")}
          </p>
          <p className="num mt-3 text-[11.5px]" style={{ color: "var(--muted-foreground)" }}>
            {targetDuration !== undefined
              ? t("ad_brief_target_duration", { value: targetDuration })
              : t("ad_brief_target_duration_unset")}
          </p>
          <p className="mt-1.5 text-[11.5px] leading-[1.55]" style={{ color: "var(--muted-foreground)" }}>
            {t("ad_brief_card_hint")}
          </p>
        </>
      )}
    </section>
  );
}
