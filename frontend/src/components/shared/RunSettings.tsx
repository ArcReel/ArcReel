import { useEffect, useState } from "react";
import { ChevronDown, SlidersHorizontal } from "lucide-react";
import { useTranslation } from "react-i18next";
import { API } from "@/api";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { useModelCapabilities } from "@/hooks/useModelCapabilities";
import { useEndpointCatalogStore } from "@/stores/endpoint-catalog-store";
import { useProjectsStore } from "@/stores/projects-store";
import { useRunSettingsStore, type GenerationOptions, type ChatOptions } from "@/stores/run-settings-store";
import { getProviderModels, getCustomProviderModels, lookupResolutions, lookupEndpointConstraints, lookupVideoAudioControl } from "@/utils/provider-models";
import type { ProviderInfo } from "@/types/provider";
import type { CustomProviderInfo } from "@/types/custom-provider";
import type { ModelCandidatesResponse } from "@/types/system";
import { ProviderModelSelect } from "./ProviderModelSelect";
import { ResolutionPicker } from "./ResolutionPicker";
import { TruncatedText } from "./TruncatedText";

const EMPTY = {};
type Catalog = { candidates: ModelCandidatesResponse; providers: ProviderInfo[]; custom: CustomProviderInfo[] };

/** 执行入口的临时覆盖，随请求传递，不写入项目或全局默认。 */
export function RunSettings({ projectName, kind, disabled = false, fixedAspectRatio = false, draftOptions, onDraftOptionsChange }: {
  projectName: string; kind: "image" | "video" | "chat"; disabled?: boolean; fixedAspectRatio?: boolean;
  draftOptions?: GenerationOptions;
  onDraftOptionsChange?: (value: GenerationOptions) => void;
}) {
  const { t, i18n } = useTranslation("dashboard");
  const [open, setOpen] = useState(draftOptions !== undefined);
  const [loaded, setLoaded] = useState<Catalog | null>(null);
  const [chatModels, setChatModels] = useState<string[]>([]);
  const [failed, setFailed] = useState(false);
  const [retry, setRetry] = useState(0);
  const savedOptions = useRunSettingsStore((s) => s.projects[projectName]?.[kind] ?? EMPTY);
  const set = useRunSettingsStore((s) => s.set);
  const project = useProjectsStore((s) => s.currentProjectData);
  const endpointTypes = useEndpointCatalogStore((s) => s.endpointToMediaType);
  const constraints = useEndpointCatalogStore((s) => s.endpointConstraints);
  const fetchEndpoints = useEndpointCatalogStore((s) => s.fetch);
  const options = draftOptions ?? savedOptions;
  const media = options as GenerationOptions;
  const chat = options as ChatOptions;
  const reference = project?.generation_mode === "reference_video";
  const caps = useModelCapabilities({
    projectName, videoBackend: media.backend, videoResolution: media.resolution,
    usesReferenceImages: reference, enabled: open && kind === "video" && Boolean(media.backend),
  });
  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    const load = async () => {
      try {
        if (kind === "chat") {
          const result = await API.getAssistantModelOptions(projectName, { signal: controller.signal });
          if (!controller.signal.aborted) setChatModels(result.models);
        } else {
          const [candidates, providers, custom] = await Promise.all([
            API.getModelCandidates({ signal: controller.signal }),
            getProviderModels({ signal: controller.signal }),
            getCustomProviderModels({ signal: controller.signal }),
          ]);
          if (controller.signal.aborted) return;
          if (custom.length > 0) await fetchEndpoints();
          if (!controller.signal.aborted) setLoaded({ candidates, providers, custom });
        }
        if (!controller.signal.aborted) setFailed(false);
      } catch {
        if (!controller.signal.aborted) setFailed(true);
      }
    };
    void load();
    return () => controller.abort();
  }, [open, kind, retry, projectName, i18n.language, fetchEndpoints]);
  const replace = (value: GenerationOptions | ChatOptions) => onDraftOptionsChange ? onDraftOptionsChange(value as GenerationOptions) : set(projectName, kind, value);
  const update = (patch: GenerationOptions | ChatOptions) => replace({ ...options, ...patch });
  const res = loaded ? lookupResolutions(loaded.providers, media.backend ?? "", loaded.custom, endpointTypes) : null;
  const endpoint = loaded ? lookupEndpointConstraints(media.backend ?? "", loaded.custom, constraints) : null;
  const audio = loaded ? lookupVideoAudioControl(loaded.providers, media.backend ?? "", reference ? "r2v" : "i2v") : null;
  const selectedName = kind === "chat" ? chat.model : loaded?.candidates.model_names[media.backend ?? ""] ?? media.backend;
  const ready = kind === "chat" ? chatModels.length > 0 : loaded !== null;
  const effortItems = [
    { value: null, label: t("run_default") },
    ...(["low", "medium", "high"] as const).map((value) => ({ value, label: t(`run_effort_${value}`) })),
  ];
  return (
    <details open={open} onToggle={(event) => setOpen(event.currentTarget.open)} className="group min-w-0 rounded-lg border border-border bg-muted/20 text-xs run-settings">
      <summary className="focus-ring flex cursor-pointer list-none items-center gap-2 rounded-lg px-3 py-2 text-muted-foreground hover:bg-muted/50 ">
        <SlidersHorizontal aria-hidden className="size-3.5 shrink-0" />
        <span className="font-medium text-foreground">{t("run_settings")}</span>
        <span className="min-w-0 flex-1 text-right"><TruncatedText text={selectedName || t("run_default")} /></span>
        <ChevronDown aria-hidden className="size-3.5 shrink-0 transition-transform group-open:rotate-180" />
      </summary>
      <fieldset disabled={disabled} className="flex min-w-0 flex-col gap-3 border-t border-border px-3 py-3">
        {failed ? <Button variant="outline" size="sm" onClick={() => setRetry((n) => n + 1)}>{t("run_load_failed")} · {t("common:retry")}</Button> : !ready ? <p className="text-muted-foreground">{t("loading")}</p> : kind === "chat" ? (
          <>
            <Label>{t("run_model")}</Label>
            <ResolutionPicker mode="select" options={chatModels} value={chat.model ?? null} onChange={(model) => update({ model: model ?? undefined })} placeholder={t("run_default")} aria-label={t("run_model")} />
            <Label>{t("run_effort")}</Label>
            <Select items={effortItems} value={chat.effort ?? null} onValueChange={(effort) => update({ effort: effort ?? undefined })}>
              <SelectTrigger aria-label={t("run_effort")}><SelectValue /></SelectTrigger>
              <SelectContent><SelectGroup>{effortItems.map((item) => <SelectItem key={item.value ?? "default"} value={item.value}>{item.label}</SelectItem>)}</SelectGroup></SelectContent>
            </Select>
          </>
        ) : loaded && (
          <>
            <Label>{t("run_model")}</Label>
            <ProviderModelSelect value={media.backend ?? ""} options={draftOptions ? (loaded.candidates.image.buckets.t2i ?? []) : loaded.candidates[kind].default} providerNames={loaded.candidates.provider_names} modelNames={loaded.candidates.model_names} onChange={(backend) => replace(backend ? { backend } : {})} allowDefault defaultLabel={t("run_default")} placeholder={t("run_default")} aria-label={t("run_model")} />
            {!!res?.options.length && <>
              <Label>{t("run_resolution")}</Label>
              <ResolutionPicker mode={res.isCustom ? "combobox" : "select"} options={res.options} value={media.resolution ?? null} onChange={(resolution) => update({ resolution })} disabled={endpoint?.sizeFixed} placeholder={t("run_default")} aria-label={t("run_resolution")} />
            </>}
            {!fixedAspectRatio && <>
              <Label>{t("run_aspect_ratio")}</Label>
              <ResolutionPicker mode="select" options={["16:9", "9:16", "1:1", "4:3", "3:4"]} value={media.aspect_ratio ?? null} onChange={(aspect_ratio) => update({ aspect_ratio: (aspect_ratio ?? undefined) as GenerationOptions["aspect_ratio"] })} disabled={endpoint?.sizeFixed} placeholder={t("run_default")} aria-label={t("run_aspect_ratio")} />
            </>}
            {kind === "video" && !!caps.supportedDurations?.length && <>
              <Label>{t("run_duration")}</Label>
              <ResolutionPicker mode="select" options={caps.supportedDurations.map(String)} value={media.duration_seconds?.toString() ?? null} onChange={(duration) => update({ duration_seconds: duration ? Number(duration) : undefined })} disabled={caps.loading} placeholder={t("run_default")} aria-label={t("run_duration")} />
            </>}
            {kind === "video" && audio === "controllable" && <Label><Switch checked={media.generate_audio ?? project?.video_generate_audio ?? true} onCheckedChange={(generate_audio) => update({ generate_audio })} />{t("run_audio")}</Label>}
            {caps.videoModelError && kind === "video" && <p role="alert" className="text-destructive">{caps.videoModelError}</p>}
          </>
        )}
        {!draftOptions && <p className="text-muted-foreground">{t("run_settings_hint")}</p>}
      </fieldset>
    </details>
  );
}
