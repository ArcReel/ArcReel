import { useAppStore } from "@/stores/app-store";
import { useAssistantStore } from "@/stores/assistant-store";

/** Prefill instructions into the Agent input and open the panel; never send automatically, leaving review and sending to the user. */
export function prefillAssistant(text: string): void {
  useAssistantStore.getState().setInput(text);
  useAppStore.getState().setAssistantPanelOpen(true);
}
