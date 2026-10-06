import { useCallback, useId, useMemo, useState } from "react";
import { Loader2, Play } from "lucide-react";
import type { TFunction } from "i18next";
import { useTranslation } from "react-i18next";
import { API } from "@/api";
import { settingsSectionPath } from "@/app-routes";
import { errMsg } from "@/utils/async";
import { cn } from "cn";
import { TruncatedText } from "@/components/shared/TruncatedText";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import type {
  CustomProviderInfo,
  EndpointDefinition,
  EndpointExtractionField,
  EndpointInputSource,
  EndpointPreviewResponse,
  EndpointStageReport,
  EndpointTestCredentials,
  EndpointTestAssets,
  EndpointTestStage,
} from "@/types";
import { FormSection } from "./endpoint-form-primitives";
import { RequestPreview, TestCard, TestField, TestSelect } from "./endpoint-test-primitives";
import { useTrialRun } from "./use-trial-run";

/** 命中值的展示文案。`image_b64` 的命中值是后端给的字节数摘要 `{ image_bytes }`，不是原串。 */
function fieldValueText(field: EndpointExtractionField, t: TFunction): string {
  const { value } = field;
  if (field.key !== "image_b64" || typeof value !== "object" || value === null || !("image_bytes" in value)) {
    return JSON.stringify(value);
  }
  return typeof value.image_bytes === "number"
    ? t("ce_check_image_bytes", { count: value.image_bytes })
    : t("ce_check_image_b64_invalid");
}

function StageReportTable({ report }: { report: EndpointStageReport }) {
  const { t } = useTranslation("dashboard");
  if (report.fields.length === 0) {
    return (
      <p className="rounded-lg border border-border px-3 py-6 text-center text-xs text-muted-foreground">
        {t("ce_check_no_fields")}
      </p>
    );
  }
  return (
    <ul className="divide-y divide-border rounded-lg border border-border">
      {report.fields.map((field) => {
        const hit = field.attempts.find((a) => a.matched);
        return (
          <li key={field.key} className="flex items-baseline gap-2.5 px-3 py-2 text-xs">
            <span
              aria-hidden
              className={cn("size-1.5 shrink-0 self-center rounded-full", hit ? "bg-good" : "bg-muted-foreground")}
            />
            <TruncatedText text={field.key} className="w-28 shrink-0 text-subtle-foreground" />
            <span className="shrink-0 font-mono text-good" translate="no">
              {hit?.path ?? "—"}
            </span>
            <span className="min-w-0 flex-1 truncate text-muted-foreground">
              {hit ? fieldValueText(field, t) : t("ce_check_no_match")}
            </span>
          </li>
        );
      })}
    </ul>
  );
}

interface EndpointTestSectionProps {
  definition: EndpointDefinition;
  providers: CustomProviderInfo[];
}

export function EndpointTestSection({ definition, providers }: EndpointTestSectionProps) {
  const { t } = useTranslation(["dashboard", "common"]);
  const idBase = useId();

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

  const ids = {
    response: `${idBase}-response`,
    previewModel: `${idBase}-preview-model`,
    baseUrl: `${idBase}-base-url`,
    apiKey: `${idBase}-api-key`,
    trialModel: `${idBase}-trial-model`,
    prompt: `${idBase}-prompt`,
  };

  return (
    <FormSection id="test" step={8} title={t("ce_section_test")} desc={t("ce_section_test_desc")}>
      <div className="flex flex-col gap-4">
        {/* 验证响应 */}
        <TestCard title={t("ce_test_check")} desc={t("ce_test_check_desc")}>
          <div className="grid grid-cols-1 gap-4 @4xl/page:grid-cols-2">
            <div className="flex min-w-0 flex-col gap-3">
              <TestSelect
                label={t("ce_check_stage")}
                value={stage}
                onValueChange={(value) => setStage(value as EndpointTestStage)}
                items={[
                  { value: "submit", label: t("ce_stage_submit") },
                  { value: "poll", label: t("ce_stage_poll") },
                  { value: "result", label: t("ce_stage_result") },
                ]}
              />
              <TestField label={t("ce_check_response_body")} htmlFor={ids.response}>
                <Textarea
                  mono
                  id={ids.response}
                  value={responseText}
                  spellCheck={false}
                  placeholder={t("ce_check_response_placeholder")}
                  onChange={(e) => setResponseText(e.target.value)}
                  className="min-h-36"
                />
              </TestField>
              <div>
                <Button onClick={() => void handleCheck()} disabled={checking || !responseText.trim()}>
                  {checking && <Loader2 aria-hidden data-icon="inline-start" className="animate-spin" />}
                  {t("ce_check_run")}
                </Button>
              </div>
              {checkError && (
                <p role="alert" className="text-xs text-warn">
                  {checkError}
                </p>
              )}
            </div>
            <div className="min-w-0">
              {checkReport ? (
                <StageReportTable report={checkReport} />
              ) : (
                <p className="rounded-lg border border-border px-3 py-8 text-center text-xs text-muted-foreground">
                  {t("ce_check_empty")}
                </p>
              )}
            </div>
          </div>
        </TestCard>

        {/* 预览请求 */}
        <TestCard title={t("ce_test_preview")} desc={t("ce_test_preview_desc")}>
          <div className="flex flex-wrap items-end gap-3">
            <TestField label={t("ce_test_model")} htmlFor={ids.previewModel} className="w-56">
              <Input
                mono
                id={ids.previewModel}
                type="text"
                autoComplete="off"
                spellCheck={false}
                value={model}
                onChange={(e) => setModel(e.target.value)}
                placeholder={definition.meta.hints?.suggested_models?.[0]?.id ?? ""}
              />
            </TestField>
            <Button variant="outline" onClick={() => void handlePreview()} disabled={previewing || !model.trim()}>
              {previewing && <Loader2 aria-hidden data-icon="inline-start" className="animate-spin" />}
              {t("ce_preview_run")}
            </Button>
          </div>
          {previewError && (
            <p role="alert" className="text-xs text-warn">
              {previewError}
            </p>
          )}
          {preview && (
            <div className="flex flex-col gap-3">
              <RequestPreview label={t("ce_stage_submit")} request={preview.submit} />
              <RequestPreview label={t("ce_stage_poll")} request={preview.poll} />
              {preview.result && <RequestPreview label={t("ce_stage_result")} request={preview.result} />}
            </div>
          )}
        </TestCard>

        {/* 测试连接 */}
        <TestCard title={t("ce_test_trial")} badge={t("ce_test_trial_billed")} desc={t("ce_test_trial_desc")}>
          <div className="grid grid-cols-1 gap-4 @4xl/page:grid-cols-2">
            <div className="flex min-w-0 flex-col gap-3">
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
                      placeholder="https://api.example.com"
                    />
                  </TestField>
                  <TestField label={t("credential_secret_label")} htmlFor={ids.apiKey}>
                    <Input
                      mono
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
              <TestField label={t("ce_test_model")} htmlFor={ids.trialModel}>
                <Input
                  mono
                  id={ids.trialModel}
                  type="text"
                  autoComplete="off"
                  spellCheck={false}
                  value={model}
                  onChange={(e) => setModel(e.target.value)}
                />
              </TestField>
              <TestField label={t("ce_trial_prompt")} htmlFor={ids.prompt}>
                <Textarea
                  id={ids.prompt}
                  value={prompt}
                  onChange={(e) => setPrompt(e.target.value)}
                  placeholder={t("ce_trial_prompt_placeholder")}
                  className="min-h-16"
                />
              </TestField>
              {assetInputs.map(({ source, required }) => {
                const label = `${t(`ce_input_source_${source}`)}${required ? t("ce_test_asset_required") : t("ce_test_asset_optional")}`;
                const id = `${idBase}-asset-${source}`;
                return (
                  <TestField key={source} label={label} htmlFor={id}>
                    <Input
                      id={id}
                      type="file"
                      accept={source === "reference_audio_files" ? "audio/*" : "image/*"}
                      multiple={source === "reference_images" || source === "reference_audio_files"}
                      required={required}
                      onChange={(e) => {
                        const files = Array.from(e.target.files ?? []);
                        setAssetFiles((current) => ({ ...current, [source]: files }));
                      }}
                    />
                  </TestField>
                );
              })}
              <div className="flex flex-wrap gap-2">
                <Button
                  onClick={() => void handleStartTrial()}
                  disabled={starting || !model.trim() || missingRequiredAsset || (run !== null && !runFinished)}
                >
                  {starting ? (
                    <Loader2 aria-hidden data-icon="inline-start" className="animate-spin" />
                  ) : (
                    <Play aria-hidden data-icon="inline-start" />
                  )}
                  {t("ce_trial_start")}
                </Button>
                {run !== null && !runFinished && (
                  <Button variant="outline" onClick={() => void trial.cancel()}>
                    {t("common:cancel")}
                  </Button>
                )}
              </div>
              {runError && (
                <p role="alert" className="text-xs text-warn">
                  {runError}
                </p>
              )}
            </div>
            <div className="min-w-0">
              {run ? (
                <div className="flex flex-col gap-3">
                  <div className="flex flex-wrap items-center gap-2 text-xs text-subtle-foreground">
                    {!runFinished && !pollStopped && (
                      <Loader2 aria-hidden className="size-3 animate-spin text-primary" />
                    )}
                    <span>{t(`ce_trial_status_${run.status}`)}</span>
                    {run.duration_seconds !== null && (
                      <span className="text-muted-foreground">
                        {t("ce_trial_duration", { seconds: run.duration_seconds })}
                      </span>
                    )}
                  </div>
                  {run.error && (
                    <p role="alert" className="text-xs wrap-break-word text-warn">
                      {run.error}
                    </p>
                  )}
                  {artifactUrl && run.media_type === "image" ? (
                    <img
                      src={artifactUrl}
                      alt={t("ce_trial_artifact")}
                      className="w-full rounded-lg border border-border bg-black object-contain"
                    />
                  ) : artifactUrl ? (
                    // eslint-disable-next-line jsx-a11y/media-has-caption -- 测试连接产物没有可用的字幕源
                    <video
                      controls
                      preload="metadata"
                      src={artifactUrl}
                      aria-label={t("ce_trial_artifact")}
                      className="w-full rounded-lg border border-border bg-black"
                    />
                  ) : run.video_url ? (
                    <TruncatedText text={run.video_url} className="font-mono text-xs text-good" />
                  ) : null}
                  {run.api_call_id !== null && (
                    <a
                      href={settingsSectionPath("usage", { record: String(run.api_call_id) })}
                      className="text-xs text-primary underline underline-offset-2 hover:text-foreground"
                    >
                      {t("ce_trial_record", { id: run.api_call_id })}
                    </a>
                  )}
                  {(["submit", "poll", "result"] as EndpointTestStage[]).map((s) => {
                    const report = run.extractions[s];
                    if (!report) return null;
                    return (
                      <div key={s} className="flex flex-col gap-1.5">
                        <span className="text-sm font-medium">{t(`ce_stage_${s}`)}</span>
                        <StageReportTable report={report} />
                      </div>
                    );
                  })}
                  {run.poll_responses.length > 0 && (
                    <p className="text-xs text-muted-foreground">
                      {t("ce_trial_poll_count", { n: run.poll_responses.length })}
                    </p>
                  )}
                </div>
              ) : (
                <p className="rounded-lg border border-border px-3 py-8 text-center text-xs text-muted-foreground">
                  {cancelled ? t("ce_trial_cancelled") : t("ce_trial_empty")}
                </p>
              )}
            </div>
          </div>
        </TestCard>
      </div>
    </FormSection>
  );
}
