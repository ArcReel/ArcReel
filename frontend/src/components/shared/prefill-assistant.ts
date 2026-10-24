import { useAppStore } from "@/stores/app-store";
import { useAssistantStore } from "@/stores/assistant-store";

/** 把一段指令预填进 Agent 输入框并打开面板；只填不发送，用户核对后自行发送。 */
export function prefillAssistant(text: string): void {
  useAssistantStore.getState().setInput(text);
  useAppStore.getState().setAssistantPanelOpen(true);
}
