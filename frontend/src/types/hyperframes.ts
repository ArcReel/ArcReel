import type { PresentationVariant } from "./presentation";

export type HyperframesStudioStatus = "stopped" | "ready";

export interface HyperframesWorkspaceStatus {
  project_name: string;
  episode: number;
  exists: boolean;
  workspace_path: string | null;
  composition_path: string | null;
  manifest_path: string | null;
  studio_status: HyperframesStudioStatus;
  studio_url: string | null;
}

export interface PrepareHyperframesWorkspaceRequest {
  narration_delivery: PresentationVariant;
}

export interface HyperframesAutoEditOptions {
  narrationDelivery: PresentationVariant;
  instruction: string;
  backgroundMusic: boolean;
}
