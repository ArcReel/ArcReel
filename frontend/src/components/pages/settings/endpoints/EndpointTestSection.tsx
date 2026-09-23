import { useCallback, useMemo, useState } from "react";
import { Loader2, Play } from "lucide-react";
import { useTranslation } from "react-i18next";
import { API } from "@/api";
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
} from "@/types";
import { FormSection, HINT_CLS, LABEL_CLS, MONO_INPUT_CLS } from "./endpoint-form-primitives";
import { RequestPreview, TestCard } from "./endpoint-test-primitives";
import { useTrialRun } from "./use-trial-run";

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
  const [assetFiles, setAssetFiles] = useState<EndpointTestAssets>({});
  const trial = useTrialRun();
  const {
    run,
    finished: runFinished,
    starting,
    error: runError,
    cancelled,
    pollStopped,
    artifactUrl,
    start: startTrial,
  } = trial;

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

  const handleStartTrial = useCallback(
    () => startTrial({ definition, parameters: { model, prompt }, credentials: credentials() }, activeAssetFiles),
    [startTrial, definition, model, prompt, credentials, activeAssetFiles],
  );

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
                  <button type="button" onClick={() => void trial.cancel()} className={GHOST_BTN_CLS}>
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
                      href={`/app/settings?section=usage&record=${run.api_call_id}`}
                      className="inline-flex text-[11.5px] text-accent-2 underline decoration-accent/40 underline-offset-2 hover:text-text"
                    >
                      {t("ce_trial_record", { id: run.api_call_id })}
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
