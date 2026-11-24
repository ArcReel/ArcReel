import { useAppStore } from "@/stores/app-store";
import { useAssistantStore } from "@/stores/assistant-store";

export function prefillAssistant(text: string): void {
  useAssistantStore.getState().setInput(text);
  useAppStore.getState().setAssistantPanelOpen(true);
}
