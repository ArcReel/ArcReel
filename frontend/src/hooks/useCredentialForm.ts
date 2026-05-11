import { useState } from "react";

import type {
  CreateAgentCredentialRequest,
  PresetProvider,
} from "@/types/agent-credential";

export interface CredentialForm {
  presetId: string;
  apiKey: string;
  baseUrl: string;
  displayName: string;
  model: string;
  haikuModel: string;
  sonnetModel: string;
  opusModel: string;
  subagentModel: string;
  setApiKey: (v: string) => void;
  setBaseUrl: (v: string) => void;
  setDisplayName: (v: string) => void;
  setModel: (v: string) => void;
  setHaikuModel: (v: string) => void;
  setSonnetModel: (v: string) => void;
  setOpusModel: (v: string) => void;
  setSubagentModel: (v: string) => void;
  /** 切预设：清空 model 字段；预设带 base_url+display_name 覆盖；自定义则清空。 */
  setPreset: (id: string) => void;
  /** 与 initial 比对，判断是否有可保存的变更（apiKey 任意非空即视为脏）。 */
  isDirty: (initial: Partial<CreateAgentCredentialRequest> | undefined) => boolean;
  /** 拼装提交 payload。 */
  buildRequest: () => CreateAgentCredentialRequest;
}

export function useCredentialForm(
  initial: Partial<CreateAgentCredentialRequest> | undefined,
  customSentinelId: string,
  presets: PresetProvider[],
): CredentialForm {
  const [presetId, setPresetId] = useState(initial?.preset_id ?? customSentinelId);
  const [apiKey, setApiKey] = useState(initial?.api_key ?? "");
  const [baseUrl, setBaseUrl] = useState(initial?.base_url ?? "");
  const [displayName, setDisplayName] = useState(initial?.display_name ?? "");
  const [model, setModel] = useState(initial?.model ?? "");
  const [haikuModel, setHaikuModel] = useState(initial?.haiku_model ?? "");
  const [sonnetModel, setSonnetModel] = useState(initial?.sonnet_model ?? "");
  const [opusModel, setOpusModel] = useState(initial?.opus_model ?? "");
  const [subagentModel, setSubagentModel] = useState(initial?.subagent_model ?? "");

  const setPreset = (id: string) => {
    if (id === presetId) return;
    setPresetId(id);
    setModel("");
    setHaikuModel("");
    setSonnetModel("");
    setOpusModel("");
    setSubagentModel("");
    if (id === customSentinelId) {
      setBaseUrl("");
      setDisplayName("");
    } else {
      const next = presets.find((p) => p.id === id);
      setBaseUrl(next?.messages_url ?? "");
      setDisplayName(next?.display_name ?? "");
    }
  };

  const isDirty = (init: Partial<CreateAgentCredentialRequest> | undefined): boolean =>
    apiKey.trim() !== "" ||
    displayName !== (init?.display_name ?? "") ||
    baseUrl !== (init?.base_url ?? "") ||
    model !== (init?.model ?? "") ||
    haikuModel !== (init?.haiku_model ?? "") ||
    sonnetModel !== (init?.sonnet_model ?? "") ||
    opusModel !== (init?.opus_model ?? "") ||
    subagentModel !== (init?.subagent_model ?? "");

  const buildRequest = (): CreateAgentCredentialRequest => ({
    preset_id: presetId,
    api_key: apiKey,
    display_name: displayName || undefined,
    base_url: baseUrl || undefined,
    model: model || undefined,
    haiku_model: haikuModel || undefined,
    sonnet_model: sonnetModel || undefined,
    opus_model: opusModel || undefined,
    subagent_model: subagentModel || undefined,
  });

  return {
    presetId,
    apiKey,
    baseUrl,
    displayName,
    model,
    haikuModel,
    sonnetModel,
    opusModel,
    subagentModel,
    setApiKey,
    setBaseUrl,
    setDisplayName,
    setModel,
    setHaikuModel,
    setSonnetModel,
    setOpusModel,
    setSubagentModel,
    setPreset,
    isDirty,
    buildRequest,
  };
}
