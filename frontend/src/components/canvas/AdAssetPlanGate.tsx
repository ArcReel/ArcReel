import { useEffect, useId, useState } from "react";
import { useTranslation } from "react-i18next";
import { useLocation } from "wouter";
import { ListChecks, Users, Landmark, Package, ShoppingBag } from "lucide-react";
import type { ProjectData } from "@/types";
import { API } from "@/api";
import { useAppStore } from "@/stores/app-store";
import { useWorkflowStore } from "@/stores/workflow-store";
import { errMsg } from "@/utils/async";
import {
  WORKSPACE_ROUTE_CHARACTERS,
  WORKSPACE_ROUTE_SCENES,
  WORKSPACE_ROUTE_PROPS,
  WORKSPACE_ROUTE_PRODUCTS,
} from "@/app-routes";

interface AdAssetPlanGateProps {
  projectName: string;
  projectData: ProjectData | null;
  /** 广告/短片项目：额外显示商品入口，并在工作流卡在确认步骤时显示确认区。 */
  isAd: boolean;
  /** 确认成功后调用（通常刷新项目数据）。 */
  onConfirmed: () => void | Promise<void>;
}

const CARD_BG =
  "linear-gradient(180deg, oklch(0.22 0.012 265 / 0.55), oklch(0.19 0.010 265 / 0.40))";
const CARD_SHADOW =
  "inset 0 1px 0 oklch(1 0 0 / 0.04), 0 8px 24px -10px oklch(0 0 0 / 0.5)";

const ASSET_LINKS = [
  {
    segment: WORKSPACE_ROUTE_CHARACTERS,
    dataKey: "characters",
    labelKey: "characters",
    hintKey: "no_characters_hint",
    Icon: Users,
  },
  {
    segment: WORKSPACE_ROUTE_SCENES,
    dataKey: "scenes",
    labelKey: "scenes",
    hintKey: "no_scenes_hint",
    Icon: Landmark,
  },
  {
    segment: WORKSPACE_ROUTE_PROPS,
    dataKey: "props",
    labelKey: "props",
    hintKey: "no_props_hint",
    Icon: Package,
  },
] as const;

const PRODUCT_LINK = {
  segment: WORKSPACE_ROUTE_PRODUCTS,
  dataKey: "products",
  labelKey: "products",
  hintKey: "no_products_hint",
  Icon: ShoppingBag,
} as const;

type AssetDataKey = (typeof ASSET_LINKS)[number]["dataKey"] | (typeof PRODUCT_LINK)["dataKey"];

function getAssetCount(projectData: ProjectData | null, key: AssetDataKey): number {
  return Object.keys(projectData?.[key] ?? {}).length;
}

/**
 * 项目概览页常驻的资产清单入口：列出角色/场景/道具（广告项目再加商品）的登记数量，
 * 数量为 0 时提示尚未创建，点击直达对应工作区登记。
 * 广告项目的脚本生成前还有一道门禁——反复出现的角色/场景/道具没有参考图时，
 * 各视频单元的生成画面会互相对不上（同一个人物、场景在不同镜头里长得不一样）。
 * 工作流状态机在 `next_action.type === "confirm_ad_asset_plan"` 时于清单下方
 * 额外要求确认，直到用户登记至少一项资产，或显式确认本项目不需要额外资产。
 * 非广告项目目前没有等价的工作流门禁，清单部分单纯作展示与创建引导。
 */
export function AdAssetPlanGate({ projectName, projectData, isAd, onConfirmed }: AdAssetPlanGateProps) {
  const { t } = useTranslation(["dashboard", "common"]);
  const [, setLocation] = useLocation();
  const checkboxId = useId();

  const plan = useWorkflowStore((s) => s.plan);
  const planKey = useWorkflowStore((s) => s.planKey);
  const refreshPlan = useWorkflowStore((s) => s.refreshPlan);

  const [noAdditionalAssets, setNoAdditionalAssets] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (!projectName || !isAd) return;
    void refreshPlan(projectName, 1);
  }, [projectName, isAd, refreshPlan]);

  // ad 恒单集：episode 固定为 1，与 workflow-store 内部的 planKey(project, episode) 拼法一致。
  const isCurrentPlan = planKey === `${projectName}::1`;
  const gateActive = isAd && isCurrentPlan && plan?.status.next_action.type === "confirm_ad_asset_plan";
  const links = isAd ? [...ASSET_LINKS, PRODUCT_LINK] : ASSET_LINKS;

  const handleConfirm = async () => {
    setSubmitting(true);
    try {
      await API.confirmAdAssetPlan(projectName, noAdditionalAssets);
      useAppStore.getState().pushToast(t("dashboard:ad_asset_plan_confirmed_toast"), "success");
      await onConfirmed();
      await refreshPlan(projectName, 1);
    } catch (err) {
      useAppStore
        .getState()
        .pushToast(t("dashboard:ad_asset_plan_confirm_failed", { message: errMsg(err) }), "error");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <section
      className="relative overflow-hidden rounded-2xl p-5"
      style={{
        border: "1px solid var(--color-accent-soft)",
        background: CARD_BG,
        boxShadow: CARD_SHADOW,
      }}
    >
      <span
        aria-hidden
        className="pointer-events-none absolute inset-x-0 top-0 h-px"
        style={{
          background: "linear-gradient(90deg, transparent, var(--color-accent-soft), transparent)",
        }}
      />

      <div className="mb-2 flex items-center gap-2.5">
        <ListChecks className="h-4 w-4" style={{ color: "var(--color-accent-2)" }} />
        <h3
          className="display-serif text-[15px] font-semibold tracking-tight"
          style={{ color: "var(--color-text)" }}
        >
          {t("dashboard:asset_checklist_title")}
        </h3>
      </div>
      <p className="text-[12.5px] leading-[1.6]" style={{ color: "var(--color-text-3)" }}>
        {gateActive ? t("dashboard:ad_asset_plan_hint") : t("dashboard:asset_checklist_hint")}
      </p>

      <div className={`mt-3.5 grid grid-cols-2 gap-2 ${isAd ? "sm:grid-cols-4" : "sm:grid-cols-3"}`}>
        {links.map(({ segment, dataKey, labelKey, hintKey, Icon }) => {
          const count = getAssetCount(projectData, dataKey);
          const empty = count === 0;
          return (
            <button
              key={segment}
              type="button"
              onClick={() => setLocation(`/${segment}`)}
              className="focus-ring flex flex-col items-start gap-1 rounded-lg px-3 py-2.5 text-left transition-colors hover:border-[var(--color-accent-soft)]"
              style={{
                border: `1px solid ${empty ? "var(--color-accent-soft)" : "var(--color-hairline)"}`,
                background: empty ? "var(--color-accent-dim)" : "oklch(0.20 0.011 265 / 0.5)",
              }}
            >
              <span className="flex w-full items-center gap-1.5">
                <Icon
                  className="h-3.5 w-3.5"
                  style={{ color: empty ? "var(--color-accent-2)" : "var(--color-text-3)" }}
                />
                <span className="text-[12.5px]" style={{ color: "var(--color-text-2)" }}>
                  {t(`dashboard:${labelKey}`)}
                </span>
                <span className="flex-1" />
                <span
                  className="num text-[11px]"
                  style={{ color: empty ? "var(--color-accent-2)" : "var(--color-text-4)" }}
                >
                  {count}
                </span>
              </span>
              {empty && (
                <span className="text-[10.5px]" style={{ color: "var(--color-text-4)" }}>
                  {t(`dashboard:${hintKey}`)}
                </span>
              )}
            </button>
          );
        })}
      </div>

      {gateActive && (
        <>
          <div className="mt-4 flex items-start gap-2">
            <input
              id={checkboxId}
              type="checkbox"
              checked={noAdditionalAssets}
              onChange={(e) => setNoAdditionalAssets(e.target.checked)}
              disabled={submitting}
              className="focus-ring mt-0.5 h-3.5 w-3.5 accent-[var(--color-accent)]"
            />
            <label
              htmlFor={checkboxId}
              className="cursor-pointer select-none text-[12.5px]"
              style={{ color: "var(--color-text-2)" }}
            >
              {t("dashboard:ad_asset_plan_no_additional_label")}
            </label>
          </div>

          <button
            type="button"
            onClick={() => void handleConfirm()}
            disabled={submitting}
            className="focus-ring mt-4 inline-flex items-center gap-1.5 rounded-md px-4 py-2 text-[13px] font-medium transition-transform disabled:cursor-not-allowed disabled:opacity-50"
            style={{
              color: "oklch(0.14 0 0)",
              background: "linear-gradient(135deg, var(--color-accent-2), var(--color-accent))",
              boxShadow:
                "inset 0 1px 0 oklch(1 0 0 / 0.35), 0 6px 18px -4px var(--color-accent-glow), 0 0 0 1px var(--color-accent-soft)",
            }}
          >
            {submitting ? t("dashboard:ad_asset_plan_confirming") : t("dashboard:ad_asset_plan_confirm_button")}
          </button>
        </>
      )}
    </section>
  );
}
