import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { useLocation, useParams, useSearch } from "wouter";
import { useTranslation } from "react-i18next";
import { AlertCircle, Bot, Brain, Film, Loader2, Mic, Palette, SlidersHorizontal, type LucideIcon } from "lucide-react";

import { API } from "@/api";
import {
  PROJECT_SETTINGS_TABS,
  ROUTE_APP_PROJECTS,
  projectSettingsPath,
  type ProjectSettingsTab,
} from "@/app-routes";
import { Alert, AlertAction, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { ProjectMemoryFiles } from "@/components/agent-memory/ProjectMemoryFiles";
import { SaveBar } from "@/components/shared/edit-unit/SaveBar";
import { PartialSaveError, useEditUnit } from "@/components/shared/edit-unit/useEditUnit";
import { isValidEpisodeTargetDuration } from "@/components/shared/episode-target-duration";
import { executingVideoModel } from "@/components/shared/model-layers";
import { countModelOverrides } from "@/components/shared/model-overrides";
import { ModelConfigSection, type ModelConfigValue } from "@/components/shared/ModelConfigSection";
import { narrationDeliveryProblem } from "@/components/shared/narration-delivery";
import { PageHeader } from "@/components/shared/page-shell/PageHeader";
import { PageShell, PageShellFooter } from "@/components/shared/page-shell/PageShell";
import { PageSidebar, type PageSidebarGroup } from "@/components/shared/page-shell/PageSidebar";
import { isValidSpeechRate } from "@/components/shared/speech-rate";
import { useDisplayNames } from "@/hooks/useDisplayNames";
import { useModelCandidates } from "@/hooks/useModelCandidates";
import { useCapabilitiesStore } from "@/stores/capabilities-store";
import type { CustomProviderInfo, NarrationDefaultsResponse, ProviderInfo } from "@/types";
import { errMsg, voidCall } from "@/utils/async";
import { normalizeRoute } from "@/utils/generation-mode";
import { getProjectDisplayName } from "@/utils/project-display";
import { getCustomProviderModels, getProviderModels } from "@/utils/provider-models";

import { AgentProfileTab } from "./project-settings/AgentProfileTab";
import { BasicsTab } from "./project-settings/BasicsTab";
import {
  buildProjectPatch,
  deriveForm,
  formEqual,
  isFormTab,
  keepSavedStylePreview,
  pendingStyleUpload,
  styleImageUrl,
  tabDirty,
  type GlobalModelDefaults,
  type ProjectFacts,
  type ProjectSettingsForm,
} from "./project-settings/project-settings-form";
import { TabHeader } from "./project-settings/SettingsBlock";
import { StyleTab } from "./project-settings/StyleTab";
import { VoiceTab } from "./project-settings/VoiceTab";

const TABS: Record<ProjectSettingsTab, { labelKey: string; icon: LucideIcon }> = {
  basics: { labelKey: "project_settings_tab_basics", icon: SlidersHorizontal },
  style: { labelKey: "project_settings_tab_style", icon: Palette },
  models: { labelKey: "project_settings_tab_models", icon: Film },
  voice: { labelKey: "project_settings_tab_voice", icon: Mic },
  memory: { labelKey: "agent_memory_project_title", icon: Brain },
  agent: { labelKey: "agent_profile_title", icon: Bot },
};

/** Sidebar groups: four Project form tabs share one edit unit; two Agent pages save independently. */
const TAB_GROUPS: { id: string; labelKey: string; tabs: ProjectSettingsTab[] }[] = [
  { id: "project", labelKey: "project_settings_group_project", tabs: ["basics", "style", "models", "voice"] },
  { id: "agent", labelKey: "project_settings_group_agent", tabs: ["memory", "agent"] },
];

function parseTab(search: string): ProjectSettingsTab {
  const value = new URLSearchParams(search).get("tab");
  return PROJECT_SETTINGS_TABS.find((tab) => tab === value) ?? "basics";
}

interface LoadedSettings {
  source: ProjectSettingsForm;
  title: string;
  facts: ProjectFacts & { sourceLanguage: string | null };
  globals: GlobalModelDefaults;
  /** Effective global Generate audio value, used for execution-model conflicts when projects follow globals. True before any saves. */
  globalGenerateAudio: boolean;
  options: {
    videoBackends: string[];
    imageBackends: string[];
    textBackends: string[];
    audioBackends: string[];
    providerNames?: Record<string, string>;
    modelNames?: Record<string, string>;
  };
  providers: ProviderInfo[];
  customProviders: CustomProviderInfo[];
  narrationDefaults: NarrationDefaultsResponse | null;
}

async function loadSettings(projectName: string, signal: AbortSignal): Promise<LoadedSettings> {
  const [configRes, projectRes, providers, customProviders, narrationDefaults, auto] = await Promise.all([
    API.getSystemConfig({ signal }),
    API.getProject(projectName, { signal }),
    getProviderModels({ signal }).catch(() => [] as ProviderInfo[]),
    getCustomProviderModels({ signal }).catch(() => [] as CustomProviderInfo[]),
    API.getNarrationDefaults({ signal }).catch(() => null),
    // Actual automatically selected models support display/capabilities only; failed reads show Auto only.
    API.getAutoBackends({ signal }).catch(() => null),
  ]);
  const settings = configRes.settings;
  const globals: GlobalModelDefaults = {
    video: settings?.default_video_backend ?? "",
    videoI2V: settings?.default_video_backend_i2v ?? "",
    videoR2V: settings?.default_video_backend_r2v ?? "",
    image: settings?.default_image_backend ?? "",
    imageT2I: settings?.default_image_backend_t2i ?? "",
    imageI2I: settings?.default_image_backend_i2i ?? "",
    textDefault: settings?.default_text_backend ?? "",
    textSimple: settings?.text_backend_simple ?? "",
    textComplex: settings?.text_backend_complex ?? "",
    auto,
  };
  const project = projectRes.project as unknown as Record<string, unknown>;
  return {
    source: deriveForm(projectRes.project, projectName, globals, projectRes.asset_fingerprints ?? null),
    title: typeof project.title === "string" ? project.title : "",
    facts: {
      contentMode: typeof project.content_mode === "string" ? project.content_mode : "narration",
      generationRoute: normalizeRoute(project.generation_mode),
      // Source language is written by content analysis, readonly here, solely choosing speaking-rate units (characters / words).
      sourceLanguage: typeof project.source_language === "string" ? project.source_language : null,
    },
    globals,
    globalGenerateAudio: settings?.video_generate_audio ?? true,
    options: {
      videoBackends: configRes.options?.video_backends ?? [],
      imageBackends: configRes.options?.image_backends ?? [],
      textBackends: configRes.options?.text_backends ?? [],
      audioBackends: configRes.options?.audio_backends ?? [],
      providerNames: configRes.options?.provider_names,
      modelNames: configRes.options?.model_names,
    },
    providers,
    customProviders,
    narrationDefaults,
  };
}

/** Project settings: sidebar tabs; four Project forms share one save bar, Agent contains project memory/profile. */
export function ProjectSettingsPage() {
  const params = useParams<{ projectName: string }>();
  const projectName = params.projectName || "";
  // Project changes rebuild whole pages, never carrying unsaved edits/in-flight requests.
  return <ProjectSettingsView key={projectName} projectName={projectName} />;
}

function ProjectSettingsView({ projectName }: { projectName: string }) {
  const { t } = useTranslation("dashboard");
  const [loaded, setLoaded] = useState<LoadedSettings | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [loadCount, setLoadCount] = useState(0);

  // react-doctor-disable-next-line react-doctor/no-set-state-after-await-in-effect -- postawait writes first check controller.signal.aborted; cleanup abort invalidates previous rounds
  useEffect(() => {
    const controller = new AbortController();
    voidCall(
      (async () => {
        try {
          const next = await loadSettings(projectName, controller.signal);
          if (controller.signal.aborted) return;
          setLoaded(next);
        } catch (error) {
          if (controller.signal.aborted) return;
          setLoadError(errMsg(error));
        }
      })(),
    );
    return () => controller.abort();
  }, [projectName, loadCount]);

  if (loaded) return <LoadedProjectSettings projectName={projectName} loaded={loaded} />;

  return (
    <SettingsShell projectName={projectName}>
      {loadError ? (
        <Alert variant="destructive">
          <AlertCircle aria-hidden />
          <AlertDescription>{t("project_settings_load_failed", { message: loadError })}</AlertDescription>
          <AlertAction>
            <Button
              variant="outline"
              size="xs"
              onClick={() => {
                setLoadError(null);
                setLoadCount((count) => count + 1);
              }}
            >
              {t("common:retry")}
            </Button>
          </AlertAction>
        </Alert>
      ) : (
        <p className="flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 aria-hidden className="size-4 animate-spin" />
          {t("common:loading")}
        </p>
      )}
    </SettingsShell>
  );
}

/** Shell: header Back returns to workbenches; sidebar tabs persist in `?tab=`. */
function SettingsShell({
  projectName,
  title,
  badges,
  children,
}: {
  projectName: string;
  /** Project display names, hidden before loading completes. */
  title?: string;
  badges?: Partial<Record<ProjectSettingsTab, ReactNode>>;
  children: ReactNode;
}) {
  const { t } = useTranslation(["dashboard", "common"]);
  const [, navigate] = useLocation();
  const tab = parseTab(useSearch());

  const groups = useMemo<PageSidebarGroup[]>(
    () =>
      TAB_GROUPS.map((group) => ({
        id: group.id,
        label: t(group.labelKey),
        items: group.tabs.map((id) => ({
          id,
          label: t(TABS[id].labelKey),
          icon: TABS[id].icon,
          href: projectSettingsPath(projectName, id),
          badge: badges?.[id],
        })),
      })),
    [t, projectName, badges],
  );

  return (
    <PageShell
      header={
        <PageHeader
          back={{
            label: t("common:back"),
            onClick: () => navigate(`${ROUTE_APP_PROJECTS}/${encodeURIComponent(projectName)}`),
          }}
          title={t("project_settings")}
          subtitle={title}
        />
      }
      sidebar={<PageSidebar label={t("project_settings")} groups={groups} activeId={tab} replace />}
      tier="constrained"
    >
      {children}
    </PageShell>
  );
}

function TabBadge({ overrides, dirty }: { overrides?: number; dirty: boolean }) {
  const { t } = useTranslation("common");
  if (!overrides && !dirty) return undefined;
  return (
    <span className="flex shrink-0 items-center gap-1.5">
      {!!overrides && (
        <span className="text-xs text-muted-foreground tabular-nums">
          {t("dashboard:project_settings_overrides_badge", { count: overrides })}
        </span>
      )}
      {dirty && <span role="img" aria-label={t("unsaved_changes")} className="size-1.5 rounded-full bg-warn" />}
    </span>
  );
}

/** Invalid Basics fields already have inline hints; saving need only identify their tab. */
function basicsInvalid(value: ProjectSettingsForm, facts: ProjectFacts): boolean {
  return (
    !isValidSpeechRate(value.speechRate) ||
    (facts.contentMode === "ad"
      ? value.adTargetDuration === null
      : !isValidEpisodeTargetDuration(value.episodeTargetDuration))
  );
}

function LoadedProjectSettings({ projectName, loaded }: { projectName: string; loaded: LoadedSettings }) {
  const { t } = useTranslation("dashboard");
  const tab = parseTab(useSearch());
  const { facts, globals } = loaded;

  const { candidates, error: candidatesError, retrying: candidatesRetrying, reload: reloadCandidates } =
    useModelCandidates();
  // Candidates are global/project-independent; reload identities follow UI languages, refreshing candidates/translations only.
  useEffect(() => {
    void reloadCandidates();
  }, [reloadCandidates]);
  const catalogNames = useMemo(
    () => ({ provider_names: loaded.options.providerNames, model_names: loaded.options.modelNames }),
    [loaded.options],
  );
  const { providerNames, modelNames } = useDisplayNames(
    loaded.providers,
    loaded.customProviders,
    catalogNames,
    candidates,
  );

  // Saves submit project PATCH then new-reference uploads. Upload failures occur after PATCH persistence; report truthfully,
  // advance saved state to PATCH results and leave only new references unsaved; another save retries uploads.
  const save = useCallback(
    async (value: ProjectSettingsForm, saved: ProjectSettingsForm): Promise<ProjectSettingsForm> => {
      if (basicsInvalid(value, facts)) {
        throw new Error(t("project_settings_invalid_fields", { tab: t("project_settings_tab_basics") }));
      }
      const narrationProblem = narrationDeliveryProblem(value.narration);
      if (narrationProblem) {
        throw new Error(t(narrationProblem === "model" ? "project_tts_model_required" : "project_narration_voice_required"));
      }
      const res = await API.updateProject(projectName, buildProjectPatch(value, saved, facts, globals));
      // Persisted grid_storyboard/video_backend affect /video-capabilities without changing query keys; explicitly invalidate.
      useCapabilitiesStore.getState().invalidate();
      const next = keepSavedStylePreview(deriveForm(res.project, projectName, globals, null), saved);
      const file = pendingStyleUpload(value, saved);
      if (!file) return next;
      let uploaded;
      try {
        uploaded = await API.uploadStyleImage(projectName, file);
      } catch (error) {
        throw new PartialSaveError(t("project_settings_style_upload_failed", { message: errMsg(error) }), {
          saved: next,
          value: { ...next, style: value.style },
          cause: error,
        });
      }
      // Fixed reference filenames require URL version changes to avoid old-image caches. Uploads return no file mtime;
      // use completion instants during this page session only; reentry uses detail-response asset fingerprints.
      const preview = styleImageUrl(projectName, uploaded.style_image, Date.now());
      return { ...next, style: { kind: "image", preview, description: uploaded.style_description, file: null } };
    },
    [projectName, facts, globals, t],
  );

  // Switching four form tabs keeps edit units mounted without guards; memory/profile tabs or page departure invoke guards.
  const allowNavigation = useCallback(
    (to: string) => {
      const url = new URL(to, "http://project-settings.invalid");
      const here = new URL(projectSettingsPath(projectName), "http://project-settings.invalid");
      return (
        decodeURIComponent(url.pathname).toLowerCase() === decodeURIComponent(here.pathname).toLowerCase() &&
        isFormTab(parseTab(url.search))
      );
    },
    [projectName],
  );

  const unit = useEditUnit({ source: loaded.source, save, isEqual: formEqual, allowNavigation });
  const { value, savedValue, setValue } = unit;

  // Pages own pending-reference preview URLs, revoking on replacement/discard/save or departure.
  const stylePreview = value.style.kind === "image" ? value.style.preview : null;
  useEffect(() => {
    if (!stylePreview?.startsWith("blob:")) return;
    return () => URL.revokeObjectURL(stylePreview);
  }, [stylePreview]);

  const overrides = countModelOverrides(value.models, value.models.generateAudio);
  const badges = {
    basics: <TabBadge dirty={tabDirty("basics", value, savedValue)} />,
    style: <TabBadge dirty={tabDirty("style", value, savedValue)} />,
    models: <TabBadge overrides={overrides} dirty={tabDirty("models", value, savedValue)} />,
    voice: <TabBadge dirty={tabDirty("voice", value, savedValue)} />,
  };

  const usesReferenceImages = facts.generationRoute === "reference_video";
  const modelValue: ModelConfigValue = {
    ...value.models,
    videoResolution: value.models.videoResolutions?.[executingVideoModel(value.models, globals, usesReferenceImages)] ?? null,
  };

  let content: ReactNode;
  switch (tab) {
    case "basics":
      content = <BasicsTab value={value} savedValue={savedValue} onChange={setValue} facts={facts} />;
      break;
    case "style":
      content = <StyleTab value={value.style} onChange={(style) => setValue((prev) => ({ ...prev, style }))} />;
      break;
    case "models":
      content = (
        <div className="flex flex-col gap-6">
          <TabHeader title={t("project_settings_tab_models")} />
          <ModelConfigSection
            projectName={projectName}
            value={modelValue}
            onChange={({ videoResolution: _videoResolution, ...next }) =>
              setValue((prev) => ({ ...prev, models: { ...prev.models, ...next } }))
            }
            providers={loaded.providers}
            customProviders={loaded.customProviders}
            options={{
              videoBackends: loaded.options.videoBackends,
              imageBackends: loaded.options.imageBackends,
              textBackends: loaded.options.textBackends,
              providerNames,
              modelNames,
            }}
            candidates={candidates}
            candidatesError={
              candidatesError ? { onRetry: () => void reloadCandidates(), retrying: candidatesRetrying } : undefined
            }
            globalDefaults={globals}
            videoGenerateAudio={value.models.generateAudio}
            globalVideoGenerateAudio={loaded.globalGenerateAudio}
            onVideoGenerateAudioChange={(generateAudio) =>
              setValue((prev) => ({ ...prev, models: { ...prev.models, generateAudio } }))
            }
            usesReferenceImages={usesReferenceImages}
            showOverrideSources
            enable={facts.contentMode === "ad" ? { duration: false } : undefined}
          />
        </div>
      );
      break;
    case "voice":
      content = (
        <VoiceTab
          value={value}
          onChange={setValue}
          generationRoute={facts.generationRoute}
          narrationDefaults={loaded.narrationDefaults}
          audioBackends={loaded.options.audioBackends}
          providerNames={providerNames}
          modelNames={modelNames}
          lifecycle={candidates?.lifecycle}
        />
      );
      break;
    case "memory":
      content = (
        <div className="flex flex-col gap-6">
          <TabHeader title={t("agent_memory_project_title")} description={t("agent_memory_project_desc")} />
          <ProjectMemoryFiles projectName={projectName} />
        </div>
      );
      break;
    case "agent":
      content = <AgentProfileTab projectName={projectName} />;
      break;
  }

  return (
    <SettingsShell
      projectName={projectName}
      title={getProjectDisplayName(loaded.title, t("untitled_project"))}
      badges={badges}
    >
      {content}
      {isFormTab(tab) && (
        <PageShellFooter>
          <SaveBar unit={unit} />
        </PageShellFooter>
      )}
    </SettingsShell>
  );
}
