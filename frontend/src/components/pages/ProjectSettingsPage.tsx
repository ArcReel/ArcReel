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
import { useEditUnit } from "@/components/shared/edit-unit/useEditUnit";
import { isValidEpisodeTargetDuration } from "@/components/shared/EpisodeTargetDurationField";
import { executingVideoModel } from "@/components/shared/LayeredModelFields";
import { countModelOverrides } from "@/components/shared/model-overrides";
import { ModelConfigSection, type ModelConfigValue } from "@/components/shared/ModelConfigSection";
import { narrationDeliveryProblem } from "@/components/shared/NarrationDeliveryFields";
import { PageHeader } from "@/components/shared/page-shell/PageHeader";
import { PageShell, PageShellFooter } from "@/components/shared/page-shell/PageShell";
import { PageSidebar, type PageSidebarGroup } from "@/components/shared/page-shell/PageSidebar";
import { isValidSpeechRate } from "@/components/shared/SpeechRateField";
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

/** 侧栏分组：「项目」四个表单分页共用一个编辑单元，「Agent」两页各管各的保存。 */
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
  /** 全局「生成有声视频」的生效值，项目跟随全局时据此判定与执行模型的矛盾。未保存过时为 true。 */
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
  const [configRes, projectRes, providers, customProviders, narrationDefaults] = await Promise.all([
    API.getSystemConfig({ signal }),
    API.getProject(projectName, { signal }),
    getProviderModels().catch(() => [] as ProviderInfo[]),
    getCustomProviderModels().catch(() => [] as CustomProviderInfo[]),
    API.getNarrationDefaults({ signal }).catch(() => null),
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
  };
  const project = projectRes.project as unknown as Record<string, unknown>;
  return {
    source: deriveForm(projectRes.project, projectName, globals),
    title: typeof project.title === "string" ? project.title : "",
    facts: {
      contentMode: typeof project.content_mode === "string" ? project.content_mode : "narration",
      generationRoute: normalizeRoute(project.generation_mode),
      // 源文语言由内容分析写入，此页只读，只用来决定语速的单位名词（字 / 词）
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

/** 项目设置：侧栏分页，「项目」组的四个表单分页共用一条保存栏，「Agent」组是项目记忆与 Agent 配置。 */
export function ProjectSettingsPage() {
  const params = useParams<{ projectName: string }>();
  const projectName = params.projectName || "";
  // 换项目即整页重建，上一个项目的未保存修改与在途请求不会带过来
  return <ProjectSettingsView key={projectName} projectName={projectName} />;
}

function ProjectSettingsView({ projectName }: { projectName: string }) {
  const { t } = useTranslation("dashboard");
  const [loaded, setLoaded] = useState<LoadedSettings | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [loadCount, setLoadCount] = useState(0);

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

/** 外壳：顶栏「返回」回到项目工作台，侧栏分页记在 `?tab=`。 */
function SettingsShell({
  projectName,
  title,
  badges,
  children,
}: {
  projectName: string;
  /** 项目显示名，加载完成前不显示。 */
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

/** 基础分页里校验未通过的字段；行内已有提示，保存时只需指出分页。 */
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
  // 候选是全局配置，与项目无关；reload 的标识随界面语言变化，语言切换时只刷新候选与译名
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

  // 一次保存依次提交项目 PATCH 与新参考图上传。上传失败时 PATCH 已经落盘，如实说明，
  // 未保存修改保留，再次保存会重新提交两步。
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
      // grid_storyboard、video_backend 落盘后 /video-capabilities 按已存值解析，查询 key 不变，需显式失效
      useCapabilitiesStore.getState().invalidate();
      const next = deriveForm(res.project, projectName, globals);
      const file = pendingStyleUpload(value, saved);
      if (!file) return next;
      let uploaded;
      try {
        uploaded = await API.uploadStyleImage(projectName, file);
      } catch (error) {
        throw new Error(t("project_settings_style_upload_failed", { message: errMsg(error) }), { cause: error });
      }
      // 参考图文件名固定，地址加版本参数，避免浏览器沿用旧图的缓存
      const preview = `${styleImageUrl(projectName, uploaded.style_image)}?v=${Date.now()}`;
      return { ...next, style: { kind: "image", preview, description: uploaded.style_description, file: null } };
    },
    [projectName, facts, globals, t],
  );

  // 四个表单分页之间切换不卸载编辑单元，不拦截；切到项目记忆、Agent 配置或离开本页时拦截
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

  // 待上传参考图的预览地址由页面持有：不再使用（换图、放弃、保存）或离开页面时收回
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
