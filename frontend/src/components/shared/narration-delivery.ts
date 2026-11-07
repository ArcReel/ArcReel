import type { NarrationDelivery } from "@/types";

/** Project narration-delivery configuration (docs/adr/0089): delivery method and TTS snapshot. */
export interface NarrationDeliveryValue {
  delivery: NarrationDelivery;
  /** "provider/model"; empty means unselected. */
  audioBackend: string;
  narrationVoice: string;
  /** null means do not send speech rate to the provider. */
  narrationSpeed: number | null;
}

export type NarrationDeliveryProblem = "model" | "voice";

/** TTS voiceover projects require a model and voice; postproduction voiceover projects do not. */
export function narrationDeliveryProblem(value: NarrationDeliveryValue): NarrationDeliveryProblem | null {
  if (value.delivery !== "use_tts") return null;
  if (!value.audioBackend) return "model";
  if (!value.narrationVoice.trim()) return "voice";
  return null;
}
