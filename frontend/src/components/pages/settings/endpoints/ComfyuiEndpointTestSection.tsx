import { useCallback, useId, useMemo, useState } from "react";
import { Loader2, Play } from "lucide-react";
import { useTranslation } from "react-i18next";
import { API } from "@/api";
import { settingsSectionPath } from "@/app-routes";
import { errMsg } from "@/utils/async";
import { cn } from "cn";
import { TruncatedText } from "@/components/shared/TruncatedText";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import type {
  ComfyuiEndpointDefinition,
  ComfyuiMediaType,
  CustomProviderInfo,
  EndpointPreviewResponse,
  EndpointTestAssets,
  EndpointTestCredentials,
  TrialRunInfo,
  TrialRunStage,
  TrialRunStageState,
} from "@/types";
import { TRIAL_RUN_STAGES } from "@/types";
import { RequestPreview, TestCard, TestField, TestSelect } from "./endpoint-test-primitives";
import { useTrialRun } from "./use-trial-run";

/** 两种媒体类型各自的分辨率档，与服务端 `packages/arcreel-market-core/src/arcreel_market_core/aspect_size.py` 的短边表同名同序。 */
const RESOLUTION_TIERS: Record<ComfyuiMediaType, readonly string[]> = {
  video: ["480p", "720p", "1080p", "4K"],
  image: ["512px", "1K", "2K", "4K"],
};

/** 项目的画幅比例只有竖屏与横屏两档，测试参数照它给。 */
const ASPECT_RATIOS = ["9:16", "16:9"] as const;

/** 分辨率下拉里「沿用 workflow 原生尺寸」那一项的取值；发请求时换回 null。 */
const NATIVE_RESOLUTION = "native";

/** 时长输入清空时按这个秒数发出，占位符把它写在空输入框里。 */
const DEFAULT_DURATION_SECONDS = 5;

/** 能从节点绑定推出素材格子的三个语义键，按渲染次序。 */
const ASSET_KEYS = ["start_image", "end_image", "reference_images"] as const;

type ComfyuiAssetKey = (typeof ASSET_KEYS)[number];

/** 四段各自打给 ComfyUI 的那条路由，测试连接的状态点上原样标出。 */
const STAGE_ROUTES: Record<TrialRunStage, string | null> = {
  submit: "POST /prompt",
  poll: "GET /history",
  result: null,
  artifact: "GET /view",
};

/**
 * 失败码的后续动作 → 说这一句该去做什么。服务端按同一份对照表给出动作（与项目页生成失败共用），
 * 前端只负责把它说成话；这张表没有的动作不编一句，宁可只留失败码与它自己的文案。
 */
const FAILURE_ACTION_TEXT: Record<string, string | undefined> = {
  configure_provider: "ce_cf_test_action_configure",
  retry: "ce_cf_test_action_retry",
};

const STAGE_DOT_CLS: Record<TrialRunStageState, string> = {
  done: "bg-good",
  pending: "bg-primary animate-breath",
  skipped: "bg-input",
};

export interface ComfyuiEndpointTestSectionProps {
  /** 当前这份草稿，含尚未保存的节点绑定：要预览的正是刚绑好的那一份。 */
  definition: ComfyuiEndpointDefinition;
  /** 可选作凭证来源的 comfyui 供应商。 */
  providers: CustomProviderInfo[];
  /**
   * 节点绑定还不成立，服务端会按定义不合法拒绝这两卡。由 `saveBlockers` 里那几条同为服务端硬
   * 闸门的原因推出，先在界面上挡住，不让用户点出一条 422。
   */
  blocked: boolean;
}

/**
 * ComfyUI 端点的端点测试两卡：预览请求与测试连接。
 *
 * 没有「验证响应」那一卡——ComfyUI 的产物提取读的是 `output` 绑定那个节点的固定三个键，没有用户
 * 可配的取值路径可验（`docs/adr/0081`），服务端的模式矩阵也已把它挡掉。
 *
 * 调用参数、凭证与素材由两卡共用：先按这组参数看渲染出来的请求体，确认无误再原样真发一次，中间
 * 换掉任何一项都会让两卡说的不是同一件事。
 */
export function ComfyuiEndpointTestSection({ definition, providers, blocked }: ComfyuiEndpointTestSectionProps) {
  const { t } = useTranslation(["dashboard", "common"]);
  const idBase = useId();
  const isVideo = definition.media_type === "video";

  const [prompt, setPrompt] = useState("");
  const [aspectRatio, setAspectRatio] = useState<string>(ASPECT_RATIOS[0]);
  const [resolution, setResolution] = useState(NATIVE_RESOLUTION);
  const [durationSeconds, setDurationSeconds] = useState<number | null>(DEFAULT_DURATION_SECONDS);
  const [assetFiles, setAssetFiles] = useState<Partial<Record<ComfyuiAssetKey, File[]>>>({});

  const [credSource, setCredSource] = useState<"provider" | "inline">(
    providers.length > 0 ? "provider" : "inline",
  );
  const [providerId, setProviderId] = useState(() => (providers[0] ? String(providers[0].id) : ""));
  const [baseUrl, setBaseUrl] = useState("");
  const [apiKey, setApiKey] = useState("");

  const [previewing, setPreviewing] = useState(false);
  const [preview, setPreview] = useState<EndpointPreviewResponse | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);

  const trial = useTrialRun();
  const { start: startTrial } = trial;

  // 素材格子只由节点绑定决定：ComfyUI 端点没有声明式那份 `inputs` 节，绑没绑读图节点就是这份
  // workflow 收不收这种素材。参考图给几个格子，少传就是在演示改图。
  const assetSlots = useMemo(
    () =>
      ASSET_KEYS.flatMap((key) => {
        const slots = definition.bindings[key]?.length ?? 0;
        return slots > 0 ? [{ key, slots }] : [];
      }),
    [definition.bindings],
  );

  const assets = useMemo(() => {
    const picked: EndpointTestAssets = {};
    for (const { key } of assetSlots) {
      if (assetFiles[key]?.length) picked[key] = assetFiles[key];
    }
    return picked;
  }, [assetFiles, assetSlots]);

  const credentials = useCallback((): EndpointTestCredentials => {
    if (credSource === "provider") return { provider_id: `custom-${providerId}` };
    return { base_url: baseUrl, api_key: apiKey };
  }, [credSource, providerId, baseUrl, apiKey]);

  const parameters = useMemo(
    () => ({
      // ComfyUI 端点没有模型名可填：模型就是这份 workflow。名字进调用记录，好让「用量」页上认得出
      // 这一笔是哪个端点发的。
      model: definition.meta.name,
      prompt,
      aspect_ratio: aspectRatio,
      resolution: resolution === NATIVE_RESOLUTION ? null : resolution,
      ...(isVideo ? { duration_seconds: durationSeconds ?? DEFAULT_DURATION_SECONDS } : {}),
    }),
    [definition.meta.name, prompt, aspectRatio, resolution, durationSeconds, isVideo],
  );

  const handlePreview = useCallback(async () => {
    setPreviewError(null);
    setPreviewing(true);
    try {
      setPreview(
        await API.previewEndpointRequest({ definition, parameters, credentials: credentials() }, { assets }),
      );
    } catch (e) {
      setPreview(null);
      setPreviewError(errMsg(e));
    } finally {
      setPreviewing(false);
    }
  }, [definition, parameters, credentials, assets]);

  const handleStartTrial = useCallback(
    () => startTrial({ definition, parameters, credentials: credentials() }, assets),
    [startTrial, definition, parameters, credentials, assets],
  );

  const running = trial.run !== null && !trial.finished;

  const ids = {
    prompt: `${idBase}-prompt`,
    duration: `${idBase}-duration`,
    baseUrl: `${idBase}-base-url`,
    apiKey: `${idBase}-api-key`,
  };

  return (
    <div className="flex flex-col gap-4">
      <div className="grid grid-cols-1 gap-3 @2xl/page:grid-cols-2 @4xl/page:grid-cols-4">
        <TestField label={t("ce_trial_prompt")} htmlFor={ids.prompt} className="@2xl/page:col-span-2 @4xl/page:col-span-4">
          <Textarea
            id={ids.prompt}
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            placeholder={t("ce_trial_prompt_placeholder")}
            className="min-h-16"
          />
        </TestField>
        <TestSelect
          label={t("ce_cf_test_aspect")}
          value={aspectRatio}
          onValueChange={setAspectRatio}
          items={ASPECT_RATIOS.map((ratio) => ({ value: ratio, label: ratio }))}
        />
        <TestSelect
          label={t("ce_cf_test_resolution")}
          value={resolution}
          onValueChange={setResolution}
          items={[
            { value: NATIVE_RESOLUTION, label: t("ce_cf_test_resolution_native") },
            ...RESOLUTION_TIERS[definition.media_type].map((tier) => ({ value: tier, label: tier })),
          ]}
        />
        {isVideo && (
          <TestField label={t("ce_cf_test_duration")} htmlFor={ids.duration}>
            <Input
              id={ids.duration}
              type="number"
              min={1}
              max={60}
              step={1}
              inputMode="numeric"
              autoComplete="off"
              placeholder={String(DEFAULT_DURATION_SECONDS)}
              value={durationSeconds ?? ""}
              onChange={(e) => {
                // 非正整数（含退格清空后的空串）落成空态，输入框据此显示占位符而不是被填回一个
                // 数字——改秒数的第一步就是清空它。空态按占位符那个秒数发出。
                const seconds = Number(e.target.value);
                setDurationSeconds(Number.isInteger(seconds) && seconds >= 1 ? seconds : null);
              }}
            />
          </TestField>
        )}
        {assetSlots.map(({ key, slots }) => {
          const id = `${idBase}-asset-${key}`;
          return (
            <TestField
              key={key}
              htmlFor={id}
              label={
                <>
                  {t(`ce_cf_key_${key}`)}
                  {key === "reference_images" && (
                    <span className="font-normal text-muted-foreground">{t("ce_cf_test_slots", { n: slots })}</span>
                  )}
                </>
              }
            >
              <Input
                id={id}
                type="file"
                accept="image/*"
                multiple={key === "reference_images"}
                onChange={(e) => {
                  const files = Array.from(e.target.files ?? []);
                  setAssetFiles((current) => ({ ...current, [key]: files }));
                }}
              />
            </TestField>
          );
        })}
      </div>

      <div className="grid grid-cols-1 gap-3 @2xl/page:grid-cols-2 @4xl/page:grid-cols-4">
        <TestSelect
          label={t("ce_trial_credentials")}
          value={credSource}
          onValueChange={(value) => setCredSource(value as "provider" | "inline")}
          items={[
            { value: "provider", label: t("ce_trial_creds_provider"), disabled: providers.length === 0 },
            { value: "inline", label: t("ce_trial_creds_inline") },
          ]}
        />
        {credSource === "provider" ? (
          <TestSelect
            label={t("ce_trial_provider")}
            value={providerId}
            onValueChange={setProviderId}
            items={providers.map((p) => ({ value: String(p.id), label: p.display_name }))}
          />
        ) : (
          <>
            <TestField label={t("base_url")} htmlFor={ids.baseUrl}>
              <Input
                id={ids.baseUrl}
                type="url"
                autoComplete="off"
                spellCheck={false}
                value={baseUrl}
                onChange={(e) => setBaseUrl(e.target.value)}
                placeholder="http://127.0.0.1:8188"
              />
            </TestField>
            <TestField label={t("credential_secret_label")} htmlFor={ids.apiKey}>
              <Input
                id={ids.apiKey}
                type="password"
                autoComplete="off"
                value={apiKey}
                onChange={(e) => setApiKey(e.target.value)}
                placeholder={t("ce_trial_key_placeholder")}
              />
            </TestField>
          </>
        )}
        <p className="self-end text-xs text-muted-foreground @2xl/page:col-span-2">{t("ce_cf_test_credentials_note")}</p>
      </div>

      {blocked && (
        <p role="status" className="text-xs text-warn">
          {t("ce_cf_test_blocked")}
        </p>
      )}

      <TestCard title={t("ce_test_preview")} desc={t("ce_cf_test_preview_desc")}>
        <div>
          <Button variant="outline" onClick={() => void handlePreview()} disabled={previewing || blocked}>
            {previewing && <Loader2 aria-hidden data-icon="inline-start" className="animate-spin" />}
            {t("ce_cf_test_preview_run")}
          </Button>
        </div>
        {previewError && (
          <p role="alert" className="text-xs text-warn">
            {previewError}
          </p>
        )}
        {preview && (
          <div className="flex flex-col gap-3">
            {preview.conversions && (
              <ConversionsTable
                conversions={preview.conversions}
                mediaType={definition.media_type}
                seedPolicy={definition.bindings.seed?.[0]?.policy ?? null}
              />
            )}
            <RequestPreview label={t("ce_cf_test_section_submit")} request={preview.submit} />
            <RequestPreview label={t("ce_cf_test_section_poll")} request={preview.poll} />
          </div>
        )}
      </TestCard>

      <TestCard title={t("ce_test_trial")} badge={t("ce_cf_test_trial_gpu")} desc={t("ce_cf_test_trial_desc")}>
        <div className="flex flex-wrap items-center gap-2">
          <Button onClick={() => void handleStartTrial()} disabled={trial.starting || blocked || running}>
            {trial.starting ? (
              <Loader2 aria-hidden data-icon="inline-start" className="animate-spin" />
            ) : (
              <Play aria-hidden data-icon="inline-start" />
            )}
            {t("ce_cf_test_trial_run")}
          </Button>
          {running && (
            <Button variant="outline" onClick={() => void trial.cancel()}>
              {t("common:cancel")}
            </Button>
          )}
          <span className="text-xs text-muted-foreground">{t("ce_cf_test_trial_note")}</span>
        </div>
        {trial.error && (
          <p role="alert" className="text-xs text-warn">
            {trial.error}
          </p>
        )}
        {trial.run ? (
          <TrialRunReport run={trial.run} artifactUrl={trial.artifactUrl} stalled={trial.pollStopped} />
        ) : (
          <p className="rounded-lg border border-border px-3 py-6 text-center text-xs text-muted-foreground">
            {trial.cancelled ? t("ce_trial_cancelled") : t("ce_cf_test_trial_empty")}
          </p>
        )}
      </TestCard>
    </div>
  );
}

/**
 * 换算说明：左边是请求侧给的，右边是真正写进 workflow 的。
 *
 * 没驱动这份 workflow 的那几维显式写出来——「我在项目页选的分辨率对这个端点根本不起作用」只有
 * 在这里说得出口，光看一份几十个节点的 JSON 是看不出来的。
 */
function ConversionsTable({
  conversions,
  mediaType,
  seedPolicy,
}: {
  conversions: NonNullable<EndpointPreviewResponse["conversions"]>;
  mediaType: ComfyuiMediaType;
  /** 种子这一维的取值策略，来自节点绑定；没绑就没有策略。 */
  seedPolicy: "random" | "keep" | null;
}) {
  const { t } = useTranslation("dashboard");
  const source = `${conversions.aspect_ratio} · ${conversions.resolution ?? t("ce_cf_test_resolution_native")}`;
  const rows: { key: string; label: string; from: string; to: string | null }[] = [
    {
      key: "size",
      label: t("ce_cf_test_conv_size"),
      from: source,
      to: conversions.width === null || conversions.height === null ? null : `${conversions.width}×${conversions.height}`,
    },
  ];
  if (mediaType === "video") {
    rows.push({
      key: "frames",
      label: t("ce_cf_test_conv_frames"),
      from:
        conversions.duration_seconds === null
          ? "—"
          : t("ce_cf_test_conv_duration", { seconds: conversions.duration_seconds }),
      to: conversions.frames === null ? null : t("ce_cf_test_conv_frame_count", { n: conversions.frames }),
    });
  }
  rows.push({
    key: "seed",
    label: t("ce_cf_test_conv_seed"),
    from: seedPolicy ?? "—",
    to: conversions.seed === null ? null : String(conversions.seed),
  });

  return (
    <div className="flex min-w-0 flex-col gap-1.5">
      <span className="text-sm font-medium">{t("ce_cf_test_conv_title")}</span>
      <dl className="divide-y divide-border rounded-lg border border-border text-xs">
        {rows.map((row) => (
          <div key={row.key} className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5 px-3 py-1.5">
            <dt className="w-16 shrink-0 text-muted-foreground">{row.label}</dt>
            <dd className="flex min-w-0 flex-wrap items-baseline gap-x-2">
              <span className="font-mono text-muted-foreground" translate="no">
                {row.from}
              </span>
              <span aria-hidden className="text-muted-foreground">
                →
              </span>
              {row.to === null ? (
                <span className="text-warn">{t("ce_cf_test_conv_unbound")}</span>
              ) : (
                <span className="font-mono tabular-nums text-foreground" translate="no">
                  {row.to}
                </span>
              )}
            </dd>
          </div>
        ))}
        {conversions.negative_prompt !== "" && (
          <div className="flex flex-wrap items-baseline gap-x-2 px-3 py-1.5">
            <dt className="w-16 shrink-0 text-muted-foreground">{t("ce_cf_key_negative_prompt")}</dt>
            <dd className="min-w-0 flex-1 wrap-break-word text-subtle-foreground">{conversions.negative_prompt}</dd>
          </div>
        )}
        {conversions.dropped_nodes.length > 0 && (
          <div className="flex flex-wrap items-baseline gap-x-2 px-3 py-1.5">
            <dt className="w-16 shrink-0 text-muted-foreground">{t("ce_cf_test_conv_dropped")}</dt>
            <dd className="min-w-0 flex-1 font-mono break-all text-subtle-foreground" translate="no">
              {conversions.dropped_nodes.join(" · ")}
            </dd>
          </div>
        )}
        <div className="flex flex-wrap items-baseline gap-x-2 px-3 py-1.5">
          <dt className="w-16 shrink-0 text-muted-foreground">{t("ce_cf_test_conv_fingerprint")}</dt>
          <dd className="min-w-0 flex-1" translate="no">
            <TruncatedText text={conversions.workflow_sha256} className="font-mono text-muted-foreground" />
          </dd>
        </div>
      </dl>
    </div>
  );
}

/** 一次测试连接走到哪儿了：四段状态点 + `prompt_id`，随后是产物卡或失败码卡。 */
function TrialRunReport({
  run,
  artifactUrl,
  stalled,
}: {
  run: TrialRunInfo;
  artifactUrl: string | null;
  stalled: boolean;
}) {
  const { t } = useTranslation("dashboard");
  const actionText = run.error_action === null ? undefined : FAILURE_ACTION_TEXT[run.error_action];
  return (
    <div className="flex flex-col gap-3">
      <ol aria-live="polite" className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs">
        {TRIAL_RUN_STAGES.map((stage) => {
          const state = run.stages[stage] ?? "pending";
          const route = STAGE_ROUTES[stage];
          return (
            <li key={stage} className="inline-flex items-baseline gap-1.5">
              <span aria-hidden className={cn("size-1.5 shrink-0 self-center rounded-full", STAGE_DOT_CLS[state])} />
              <span className={state === "done" ? "text-subtle-foreground" : "text-muted-foreground"}>
                {t(`ce_cf_test_stage_${stage}`)}
              </span>
              {route && (
                <span className="font-mono text-muted-foreground" translate="no">
                  {route}
                </span>
              )}
              <span className="sr-only">{t(`ce_cf_test_stage_state_${state}`)}</span>
            </li>
          );
        })}
      </ol>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
        <span>{t(`ce_trial_status_${run.status}`)}</span>
        {run.provider_job_id !== null && (
          <span className="font-mono break-all" translate="no">
            prompt_id {run.provider_job_id}
          </span>
        )}
        {run.duration_seconds !== null && (
          <span>{t("ce_cf_test_clip_length", { seconds: run.duration_seconds })}</span>
        )}
        {run.api_call_id !== null && (
          <a
            href={settingsSectionPath("usage", { record: String(run.api_call_id) })}
            className="text-primary underline underline-offset-2 hover:text-foreground"
          >
            {t("ce_trial_record", { id: run.api_call_id })}
          </a>
        )}
      </div>
      {stalled && <p className="text-xs text-warn">{t("ce_cf_test_poll_stopped")}</p>}
      {artifactUrl &&
        (run.media_type === "image" ? (
          <img
            src={artifactUrl}
            alt={t("ce_trial_artifact")}
            className="w-full rounded-lg border border-border bg-black object-contain"
          />
        ) : (
          // eslint-disable-next-line jsx-a11y/media-has-caption -- 测试连接产物没有可用的字幕源
          <video
            controls
            preload="metadata"
            src={artifactUrl}
            aria-label={t("ce_trial_artifact")}
            className="w-full rounded-lg border border-border bg-black"
          />
        ))}
      {run.error !== null && (
        <Alert variant="destructive">
          {run.error_code !== null && (
            <AlertTitle>
              <span className="font-mono" translate="no">
                {run.error_code}
              </span>
            </AlertTitle>
          )}
          <AlertDescription>
            <p className="wrap-break-word">{run.error}</p>
            {actionText !== undefined && <p>{t(actionText)}</p>}
          </AlertDescription>
        </Alert>
      )}
    </div>
  );
}
