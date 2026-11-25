import type { NarrationDelivery } from "@/types";

export interface NarrationDeliveryValue {
  override: boolean;
  delivery: NarrationDelivery;
  audioBackend: string;
  narrationVoice: string;
  narrationSpeed: number | null;
}

export type NarrationDeliveryProblem = "model" | "voice";

export function narrationDeliveryProblem(value: NarrationDeliveryValue): NarrationDeliveryProblem | null {
  if (!value.override) return null;
  if (!value.audioBackend) return "model";
  if (!value.narrationVoice.trim()) return "voice";
  return null;
}
