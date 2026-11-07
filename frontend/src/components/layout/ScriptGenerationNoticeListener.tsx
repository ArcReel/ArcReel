import { useScriptGenerationNotice } from "@/hooks/useScriptGenerationNotice";

/**
 * Listener component without UI: isolate useScriptGenerationNotice's assistant-store subscription in a leaf,
 * avoiding whole-layout rerenders on every Agent-stream update if attached directly to StudioLayout. Persistent mounting retains its deduplication
 * records across session/project switches.
 */
export function ScriptGenerationNoticeListener() {
  useScriptGenerationNotice();
  return null;
}
