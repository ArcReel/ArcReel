import type { AssetStatus } from "@/types";

export type ShotStatus = "ready" | "storyboard" | "pending";

/** Storyboard display status: group generated-asset states into three tiers. */
export function statusFromAssets(assetStatus: AssetStatus | undefined | null): ShotStatus {
  if (assetStatus === "completed") return "ready";
  if (assetStatus === "storyboard_ready") return "storyboard";
  return "pending";
}
