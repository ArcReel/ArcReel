import { useCallback, useEffect, useMemo, useState } from "react";
import { Loader2, Play } from "lucide-react";
import { useTranslation } from "react-i18next";
import { API, ApiRequestError } from "@/api";
import { errMsg } from "@/utils/async";
import {
  ACCENT_BTN_SM_CLS,
  ACCENT_BUTTON_STYLE,
  GHOST_BTN_CLS,
  INPUT_CLS,
} from "@/components/ui/darkroom-tokens";
import type {
  CustomProviderInfo,
  EndpointDefinition,
  EndpointInputSource,
  EndpointPreviewResponse,
  EndpointStageReport,
  EndpointTestCredentials,
  EndpointTestAssets,
  EndpointTestStage,
  PreviewedRequest,
  TrialRunInfo,
} from "@/types";
import { FormSection, HINT_CLS, LABEL_CLS, MONO_INPUT_CLS } from "./endpoint-form-primitives";

const TRIAL_POLL_INTERVAL_MS = 2000;
const TRIAL_POLL_MAX_CONSECUTIVE_FAILURES = 5;

function TestCard({
  title,
  badge,
  desc,
  children,
}: {
  title: string;
  badge?: string;
  desc: string;
  children: React.ReactNode;
}) {
  return (
    <div className="rounded-[8px] border border-hairline-soft bg-bg-grad-a/30 p-3.5">
      <div className="flex items-center gap-2">
        <span className="text-[13px] font-medium text-text">{title}</span>
        {badge && <span className="text-[11px] text-warm-bright/90">{badge}</span>}
      </div>
      <p className="mb-2.5 mt-0.5 text-[12px] leading-[1.55] text-text-3">{desc}</p>
      {children}
    </div>
  );
}

function RequestPreview({ label, request }: { label: string; request: PreviewedRequest }) {
  return (
    <div>
      <span className={LABEL_CLS}>{label}</span>
      <pre className="overflow-x-auto rounded-[8px] border border-hairline-soft bg-bg-grad-a/40 p-3 font-mono text-[11.5px] leading-[1.6] text-text-2">
        {`${request.method} ${request.url}\n`}
        {Object.entries(request.headers)
          .map(([k, v]) => `${k}: ${v}`)
          .join("\n")}
        {request.body === null || request.body === undefined
          ? ""
          : `\n\n${JSON.stringify(request.body, null, 2)}`}
      </pre>
    </div>
  );
}

function StageReportTable({ report }: { report: EndpointStageReport }) {
  const { t } = useTranslation("dashboard");
  return (
    <div className="overflow-hidden rounded-[8px] border border-hairline">
      {report.fields.length === 0 && (
        <div className="px-3 py-6 text-center text-[12px] text-text-3">{t("ce_check_no_fields")}</div>
      )}
      {report.fields.map((field) => {
        const hit = field.attempts.find((a) => a.matched);
        return (
          <div
            key={field.key}
            className="flex items-baseline gap-2.5 border-b border-hairline-soft px-3 py-2 last:border-b-0"
          >
            <span
              aria-hidden
              className={`h-1.5 w-1.5 shrink-0 self-center rounded-full ${hit ? "bg-good" : "bg-text-4"}`}
            />
            <span className="w-28 shrink-0 truncate text-[12px] text-text-2" title={field.key}>
              {field.key}
            </span>
            <span className="shrink-0 font-mono text-[10.5px] text-good/85">{hit?.path ?? "—"}</span>
            <span className="min-w-0 flex-1 truncate text-[11.5px] text-text-3">
              {hit ? JSON.stringify(field.value) : t("ce_check_no_match")}
            </span>
          </div>
        );
      })}
    </div>
  );
}

interface EndpointTestSectionProps {
  definition: EndpointDefinition;
  providers: CustomProviderInfo[];
}

export function EndpointTestSection({ definition, providers }: EndpointTestSectionProps) {
  const { t } = useTranslation(["dashboard", "common"]);

  // --- 验证响应 ---
  const [stage, setStage] = useState<EndpointTestStage>("poll");
  const [responseText, setResponseText] = useState("");
  const [checking, setChecking] = useState(false);
  const [checkReport, setCheckReport] = useState<EndpointStageReport | null>(null);
  const [checkError, setCheckError] = useState<string | null>(null);

  // --- 预览请求 ---
  const [model, setModel] = useState("");
  const [prompt, setPrompt] = useState("");
  const [previewing, setPreviewing] = useState(false);
  const [preview, setPreview] = useState<EndpointPreviewResponse | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);

  // --- 测试连接 ---
  const [credSource, setCredSource] = useState<"provider" | "inline">(
    providers.length > 0 ? "provider" : "inline",
  );
  const [providerId, setProviderId] = useState(() => (providers[0] ? String(providers[0].id) : ""));
  const [baseUrl, setBaseUrl] = useState(definition.meta.hints?.base_url ?? "");
  const [apiKey, setApiKey] = useState("");
  const [starting, setStarting] = useState(false);
  const [run, setRun] = useState<TrialRunInfo | null>(null);
  const [runError, setRunError] = useState<string | null>(null);
  const [artifact, setArtifact] = useState<{ runId: string; url: string } | null>(null);
  const [cancelled, setCancelled] = useState(false);
  // 轮询放弃态：连续失败达上限后只停读回，不动 run——服务端名额仍被占用，
  // runId 与取消入口必须保留，否则重新创建会撞 trial_run_already_running。
  const [pollStopped, setPollStopped] = useState(false);
  const [assetFiles, setAssetFiles] = useState<EndpointTestAssets>({});

  const assetInputs = useMemo(() => {
    const sources = new Map<EndpointInputSource, boolean>();
    for (const spec of Object.values(definition.inputs ?? {})) {
      sources.set(spec.source, (sources.get(spec.source) ?? false) || (spec.required ?? false));
    }
    return Array.from(sources, ([source, required]) => ({ source, required }));
  }, [definition.inputs]);
  const activeAssetFiles = useMemo(() => {
    const files: EndpointTestAssets = {};
    for (const { source } of assetInputs) {
      if (assetFiles[source]?.length) files[source] = assetFiles[source];
    }
    return files;
  }, [assetFiles, assetInputs]);
  const missingRequiredAsset = assetInputs.some(
    ({ source, required }) => required && !activeAssetFiles[source]?.length,
  );

  const credentials = useCallback((): EndpointTestCredentials => {
    if (credSource === "provider") return { provider_id: `custom-${providerId}` };
    return { base_url: baseUrl, api_key: apiKey };
  }, [credSource, providerId, baseUrl, apiKey]);

  const runId = run?.id ?? null;
  const runFinished = run !== null && (run.status === "succeeded" || run.status === "failed");
  const artifactRunId = run?.has_artifact ? run.id : null;
  const artifactUrl = artifact?.runId === artifactRunId ? artifact.url : null;

  useEffect(() => {
    if (!artifactRunId) return;
    const controller = new AbortController();
    let objectUrl: string | null = null;
    void API.getTrialRunArtifact(artifactRunId, { signal: controller.signal })
      .then((blob) => {
        if (controller.signal.aborted) return;
        objectUrl = URL.createObjectURL(blob);
        setArtifact({ runId: artifactRunId, url: objectUrl });
      })
      .catch((e) => {
        if (!controller.signal.aborted) setRunError(errMsg(e));
      });
    return () => {
      controller.abort();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [artifactRunId]);

  // 试跑是进程内异步 run：创建后轮询读回，终态即停。递归 setTimeout 保证上一次
  // 读回落地后才排下一次，响应慢于间隔时不会堆积并发请求。
  useEffect(() => {
    if (!runId || runFinished || pollStopped) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    // 连续失败达到上限即停：run 被删或凭证过期时 401/404 不会自愈，无限重试只会刷请求。
    // 单次失败不弃轮询，瞬时网络抖动在下一轮成功后计数归零。
    let consecutiveFailures = 0;
    const poll = () => {
      void API.getTrialRun(runId, { signal: controller.signal })
        .then((next) => {
          if (controller.signal.aborted) return;
          consecutiveFailures = 0;
          setRunError(null);
          setRun(next);
          timer = setTimeout(poll, TRIAL_POLL_INTERVAL_MS);
        })
        .catch((e) => {
          if (controller.signal.aborted) return;
          setRunError(errMsg(e));
          // 明确的 404 表示 run 已不在服务端（TTL 过期或重启丢失），名额已释放，
          // 就地清空本地状态；只有瞬时网络/服务错误才走重试与放弃计数。
          if (e instanceof ApiRequestError && e.status === 404) {
            setRun(null);
            return;
          }
          consecutiveFailures += 1;
          if (consecutiveFailures < TRIAL_POLL_MAX_CONSECUTIVE_FAILURES) {
            timer = setTimeout(poll, TRIAL_POLL_INTERVAL_MS);
          } else {
            setPollStopped(true);
          }
        });
    };
    timer = setTimeout(poll, TRIAL_POLL_INTERVAL_MS);
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [runId, runFinished, pollStopped]);

  const handleCheck = useCallback(async () => {
    setCheckError(null);
    setChecking(true);
    try {
      let body: unknown = responseText;
      try {
        body = JSON.parse(responseText);
      } catch {
        // 非 JSON 文本原样送服务端，由它给出解析层面的判定。
      }
      setCheckReport(await API.checkEndpointResponse({ definition, stage, response_body: body }));
    } catch (e) {
      setCheckReport(null);
      setCheckError(errMsg(e));
    } finally {
      setChecking(false);
    }
  }, [definition, stage, responseText]);

  const handlePreview = useCallback(async () => {
    setPreviewError(null);
    setPreviewing(true);
    try {
      setPreview(
        await API.previewEndpointRequest(
          {
            definition,
            parameters: { model, prompt },
            credentials: credSource === "inline" && !baseUrl && !apiKey ? undefined : credentials(),
          },
          { assets: activeAssetFiles },
        ),
      );
    } catch (e) {
      setPreview(null);
      setPreviewError(errMsg(e));
    } finally {
      setPreviewing(false);
    }
  }, [definition, model, prompt, credSource, baseUrl, apiKey, credentials, activeAssetFiles]);

  const handleStartTrial = useCallback(async () => {
    setRunError(null);
    setCancelled(false);
    setPollStopped(false);
    setStarting(true);
    try {
      setRun(
        await API.createTrialRun(
          {
            definition,
            parameters: { model, prompt },
            credentials: credentials(),
          },
          activeAssetFiles,
        ),
      );
    } catch (e) {
      setRun(null);
      setRunError(errMsg(e));
    } finally {
      setStarting(false);
    }
  }, [definition, model, prompt, credentials, activeAssetFiles]);

  // 取消会让服务端连同结果一起丢弃这次 run，回读只会拿到 404；就地清空本地状态，
  // 让「开始测试」重新可用。远端任务不受影响，已经发生的费用照算。
  const handleCancelTrial = useCallback(async () => {
    if (!runId) return;
    try {
      await API.cancelTrialRun(runId);
    } catch (e) {
      // 404 即 run 已不在服务端，无可取消——照常清理本地状态解除锁定。
      if (!(e instanceof ApiRequestError && e.status === 404)) {
        setRunError(errMsg(e));
        return;
      }
    }
    setRun(null);
    setRunError(null);
    setPollStopped(false);
    setCancelled(true);
  }, [runId]);

  return (
    <FormSection id="test" step={8} title={t("ce_section_test")} desc={t("ce_section_test_desc")}>
      <div className="space-y-3">
        {/* 验证响应 */}
        <TestCard title={t("ce_test_check")} desc={t("ce_test_check_desc")}>
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
            <div>
              <label className="block">
                <span className={LABEL_CLS}>{t("ce_check_stage")}</span>
                <select
                  value={stage}
                  onChange={(e) => setStage(e.target.value as EndpointTestStage)}
                  className={INPUT_CLS}
                >
                  <option value="submit">{t("ce_stage_submit")}</option>
                  <option value="poll">{t("ce_stage_poll")}</option>
                  <option value="result">{t("ce_stage_result")}</option>
                </select>
              </label>
              <textarea
                value={responseText}
                spellCheck={false}
                aria-label={t("ce_check_response_body")}
                placeholder={t("ce_check_response_placeholder")}
                onChange={(e) => setResponseText(e.target.value)}
                className={`${INPUT_CLS} mt-2 h-36 resize-y font-mono text-[11.5px]`}
              />
              <button
                type="button"
                onClick={() => void handleCheck()}
                disabled={checking || !responseText.trim()}
                className={`${ACCENT_BTN_SM_CLS} mt-2`}
                style={ACCENT_BUTTON_STYLE}
              >
                {checking && <Loader2 className="h-3 w-3 motion-safe:animate-spin" aria-hidden />}
                {t("ce_check_run")}
              </button>
              {checkError && (
                <p role="alert" className="mt-2 text-[12px] text-warm-bright">
                  {checkError}
                </p>
              )}
            </div>
            <div>
              {checkReport ? (
                <StageReportTable report={checkReport} />
              ) : (
                <div className="rounded-[8px] border border-hairline px-3 py-8 text-center text-[12px] text-text-3">
                  {t("ce_check_empty")}
                </div>
              )}
            </div>
          </div>
        </TestCard>

        {/* 预览请求 */}
        <TestCard title={t("ce_test_preview")} desc={t("ce_test_preview_desc")}>
          <div className="flex flex-wrap items-end gap-3">
            <label className="block w-56">
              <span className={LABEL_CLS}>{t("ce_test_model")}</span>
              <input
                type="text"
                value={model}
                onChange={(e) => setModel(e.target.value)}
                placeholder={definition.meta.hints?.suggested_models?.[0]?.id ?? ""}
                className={`${INPUT_CLS} ${MONO_INPUT_CLS}`}
              />
            </label>
            <button
              type="button"
              onClick={() => void handlePreview()}
              disabled={previewing || !model.trim()}
              className={GHOST_BTN_CLS}
            >
              {previewing && <Loader2 className="h-3 w-3 motion-safe:animate-spin" aria-hidden />}
              {t("ce_preview_run")}
            </button>
          </div>
          {previewError && (
            <p role="alert" className="mt-2 text-[12px] text-warm-bright">
              {previewError}
            </p>
          )}
          {preview && (
            <div className="mt-3 space-y-3">
              <RequestPreview label={t("ce_stage_submit")} request={preview.submit} />
              <RequestPreview label={t("ce_stage_poll")} request={preview.poll} />
              {preview.result && (
                <RequestPreview label={t("ce_stage_result")} request={preview.result} />
              )}
            </div>
          )}
        </TestCard>

        {/* 测试连接 */}
        <TestCard
          title={t("ce_test_trial")}
          badge={t("ce_test_trial_billed")}
          desc={t("ce_test_trial_desc")}
        >
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-[260px_1fr]">
            <div className="space-y-3">
              <label className="block">
                <span className={LABEL_CLS}>{t("ce_trial_credentials")}</span>
                <select
                  value={credSource}
                  onChange={(e) => setCredSource(e.target.value as "provider" | "inline")}
                  className={INPUT_CLS}
                >
                  <option value="provider" disabled={providers.length === 0}>
                    {t("ce_trial_creds_provider")}
                  </option>
                  <option value="inline">{t("ce_trial_creds_inline")}</option>
                </select>
              </label>
              {credSource === "provider" ? (
                <label className="block">
                  <span className={LABEL_CLS}>{t("ce_trial_provider")}</span>
                  <select
                    value={providerId}
                    onChange={(e) => setProviderId(e.target.value)}
                    className={INPUT_CLS}
                  >
                    {providers.map((p) => (
                      <option key={p.id} value={String(p.id)}>
                        {p.display_name}
                      </option>
                    ))}
                  </select>
                </label>
              ) : (
                <>
                  <label className="block">
                    <span className={LABEL_CLS}>{t("base_url")}</span>
                    <input
                      type="url"
                      value={baseUrl}
                      onChange={(e) => setBaseUrl(e.target.value)}
                      placeholder="https://api.example.com"
                      className={`${INPUT_CLS} ${MONO_INPUT_CLS}`}
                    />
                  </label>
                  <label className="block">
                    <span className={LABEL_CLS}>{t("api_key_label")}</span>
                    <input
                      type="password"
                      autoComplete="off"
                      value={apiKey}
                      onChange={(e) => setApiKey(e.target.value)}
                      placeholder={t("ce_trial_key_placeholder")}
                      className={`${INPUT_CLS} ${MONO_INPUT_CLS}`}
                    />
                  </label>
                </>
              )}
              <label className="block">
                <span className={LABEL_CLS}>{t("ce_test_model")}</span>
                <input
                  type="text"
                  value={model}
                  onChange={(e) => setModel(e.target.value)}
                  className={`${INPUT_CLS} ${MONO_INPUT_CLS}`}
                />
              </label>
              <label className="block">
                <span className={LABEL_CLS}>{t("ce_trial_prompt")}</span>
                <textarea
                  value={prompt}
                  onChange={(e) => setPrompt(e.target.value)}
                  placeholder={t("ce_trial_prompt_placeholder")}
                  className={`${INPUT_CLS} h-16 resize-y`}
                />
              </label>
              {assetInputs.map(({ source, required }) => (
                <label key={source} className="block">
                  <span className={LABEL_CLS}>
                    {t(`ce_input_source_${source}`)}
                    {required ? t("ce_test_asset_required") : t("ce_test_asset_optional")}
                  </span>
                  <input
                    type="file"
                    accept={source === "reference_audio_files" ? "audio/*" : "image/*"}
                    multiple={source === "reference_images" || source === "reference_audio_files"}
                    required={required}
                    aria-label={`${t(`ce_input_source_${source}`)}${required ? t("ce_test_asset_required") : t("ce_test_asset_optional")}`}
                    onChange={(e) => {
                      const files = Array.from(e.target.files ?? []);
                      setAssetFiles((current) => ({ ...current, [source]: files }));
                    }}
                    className={`${INPUT_CLS} file:mr-3 file:rounded file:border-0 file:bg-bg-grad-a file:px-2 file:py-1 file:text-text-2`}
                  />
                </label>
              ))}
              <div className="flex gap-2">
                <button
                  type="button"
                  onClick={() => void handleStartTrial()}
                  disabled={starting || !model.trim() || missingRequiredAsset || (run !== null && !runFinished)}
                  className={ACCENT_BTN_SM_CLS}
                  style={ACCENT_BUTTON_STYLE}
                >
                  <Play className="h-3 w-3" aria-hidden />
                  {t("ce_trial_start")}
                </button>
                {run !== null && !runFinished && (
                  <button type="button" onClick={() => void handleCancelTrial()} className={GHOST_BTN_CLS}>
                    {t("common:cancel")}
                  </button>
                )}
              </div>
              {runError && (
                <p role="alert" className="text-[12px] text-warm-bright">
                  {runError}
                </p>
              )}
            </div>
            <div>
              {run ? (
                <div className="space-y-2.5">
                  <div className="flex items-center gap-2 text-[12px] text-text-2">
                    {!runFinished && !pollStopped && (
                      <Loader2 className="h-3 w-3 motion-safe:animate-spin text-accent-2" aria-hidden />
                    )}
                    <span>{t(`ce_trial_status_${run.status}`)}</span>
                    {run.duration_seconds !== null && (
                      <span className="text-text-3">
                        {t("ce_trial_duration", { seconds: run.duration_seconds })}
                      </span>
                    )}
                  </div>
                  {run.error && (
                    <p role="alert" className="text-[12px] leading-[1.55] text-warm-bright">
                      {run.error}
                    </p>
                  )}
                  {artifactUrl ? (
                    // eslint-disable-next-line jsx-a11y/media-has-caption -- 测试连接产物没有可用的字幕源
                    <video
                      controls
                      preload="metadata"
                      src={artifactUrl}
                      aria-label={t("ce_trial_artifact")}
                      className="w-full rounded-[8px] border border-hairline bg-black"
                    />
                  ) : run.video_url ? (
                    <p className="truncate font-mono text-[11.5px] text-good/85">{run.video_url}</p>
                  ) : null}
                  {run.api_call_id !== null && (
                    <a
                      href={`/app/settings?section=usage&call_id=${run.api_call_id}#usage-call-${run.api_call_id}`}
                      className="inline-flex text-[11.5px] text-accent-2 underline decoration-accent/40 underline-offset-2 hover:text-text"
                    >
                      {t("ce_trial_ledger", { id: run.api_call_id })}
                    </a>
                  )}
                  {(["submit", "poll", "result"] as EndpointTestStage[]).map((s) => {
                    const report = run.extractions[s];
                    if (!report) return null;
                    return (
                      <div key={s}>
                        <span className={LABEL_CLS}>{t(`ce_stage_${s}`)}</span>
                        <StageReportTable report={report} />
                      </div>
                    );
                  })}
                  {run.poll_responses.length > 0 && (
                    <span className={HINT_CLS}>
                      {t("ce_trial_poll_count", { n: run.poll_responses.length })}
                    </span>
                  )}
                </div>
              ) : (
                <div className="rounded-[8px] border border-hairline px-3 py-8 text-center text-[12px] text-text-3">
                  {cancelled ? t("ce_trial_cancelled") : t("ce_trial_empty")}
                </div>
              )}
            </div>
          </div>
        </TestCard>
      </div>
    </FormSection>
  );
}
