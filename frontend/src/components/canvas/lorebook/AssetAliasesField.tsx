import { useId, useState } from "react";
import { useTranslation } from "react-i18next";
import { X } from "lucide-react";
import { API } from "@/api";
import { useAppStore } from "@/stores/app-store";
import { useProjectsStore } from "@/stores/projects-store";
import { errMsg } from "@/utils/async";

type AliasAssetType = "character" | "scene" | "prop";

interface AssetAliasesFieldProps {
  projectName: string;
  name: string;
  assetType: AliasAssetType;
  aliases: string[];
  /** 只读展示（引导演示项目）：只列出别名，不渲染增删入口。 */
  readOnly?: boolean;
}

const UPDATE: Record<AliasAssetType, (project: string, name: string, updates: Record<string, unknown>) => Promise<unknown>> = {
  character: (project, name, updates) => API.updateCharacter(project, name, updates),
  scene: (project, name, updates) => API.updateProjectScene(project, name, updates),
  prop: (project, name, updates) => API.updateProjectProp(project, name, updates),
};

/**
 * 资产卡片上的别名：增删即保存。别名只供 AI 规划时认人参考，不参与引用；后端按名称判等去空白、
 * 去重并去掉与资产同名的一项。
 */
export function AssetAliasesField({ projectName, name, assetType, aliases, readOnly = false }: AssetAliasesFieldProps) {
  const { t } = useTranslation("assets");
  const [input, setInput] = useState("");
  const [saving, setSaving] = useState(false);
  const inputId = useId();

  if (readOnly && aliases.length === 0) return null;

  const save = async (next: string[]): Promise<boolean> => {
    setSaving(true);
    try {
      await UPDATE[assetType](projectName, name, { aliases: next });
      await useProjectsStore.getState().refreshProject(projectName);
      return true;
    } catch (err) {
      useAppStore.getState().pushToast(t("aliases_save_failed", { message: errMsg(err) }), "error");
      return false;
    } finally {
      setSaving(false);
    }
  };

  const add = async () => {
    const alias = input.trim();
    if (!alias || saving) return;
    if (await save([...aliases, alias])) setInput("");
  };

  return (
    <div className="mb-3">
      <label
        htmlFor={inputId}
        className="text-[10px] font-semibold uppercase tracking-[0.12em]"
        style={{ color: "var(--muted-foreground)" }}
      >
        {t("aliases_label")}
      </label>
      <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
        {aliases.map((alias) => (
          <span
            key={alias}
            className="inline-flex items-center gap-1 rounded-md border border-border px-2 py-0.5 text-[12px] text-subtle-foreground"
          >
            {alias}
            {!readOnly && (
              <button
                type="button"
                onClick={() => void save(aliases.filter((item) => item !== alias))}
                disabled={saving}
                aria-label={t("aliases_remove", { alias })}
                className="focus-ring rounded-sm text-muted-foreground hover:text-foreground disabled:cursor-not-allowed disabled:opacity-40"
              >
                <X className="h-3 w-3" />
              </button>
            )}
          </span>
        ))}
        {!readOnly && (
          <input
            id={inputId}
            type="text"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.nativeEvent.isComposing) {
                e.preventDefault();
                void add();
              }
            }}
            disabled={saving}
            placeholder={t("aliases_placeholder")}
            className="focus-ring min-w-[8rem] flex-1 rounded-md border border-border bg-transparent px-2 py-0.5 text-[12px] text-subtle-foreground outline-none disabled:opacity-60"
          />
        )}
      </div>
      {!readOnly && <p className="mt-1 text-[11px] text-muted-foreground">{t("aliases_hint")}</p>}
    </div>
  );
}
