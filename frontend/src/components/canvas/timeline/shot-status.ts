import type { AssetStatus } from "@/types";

export type ShotStatus = "ready" | "storyboard" | "pending";

/** 分镜的展示状态：按生成资产的状态归到三档。 */
export function statusFromAssets(assetStatus: AssetStatus | undefined | null): ShotStatus {
  if (assetStatus === "completed") return "ready";
  if (assetStatus === "storyboard_ready") return "storyboard";
  return "pending";
}
