import { useMemo } from "react";
import { useTranslation } from "react-i18next";
import { Input } from "@/components/ui/input";
import { useAppStore } from "@/stores/app-store";
import type {
  EndpointCapabilities,
  EndpointDefinition,
  EndpointInputEncoding,
  EndpointInputSource,
  EndpointPathItem,
  EndpointStandardStatus,
} from "@/types";
import {
  type EndpointArtifactKey,
  HTTP_METHODS,
  INPUT_ENCODINGS,
  MEDIA_TYPE_FORM_PROFILES,
  STANDARD_STATUSES,
  definitionMediaType,
  readPaths,
  renameKey,
  writePaths,
} from "./endpoint-definition-draft";
import {
  AddRowButton,
  CheckboxField,
  FormSection,
  HINT_CLS,
  LABEL_CLS,
  PathsEditor,
  RowDeleteButton,
  SelectField,
  TextField,
  VariableChips,
} from "./endpoint-form-primitives";
import { JsonBodyEditor } from "./JsonBodyEditor";

interface EndpointFormProps {
  definition: EndpointDefinition;
  onChange: (next: EndpointDefinition) => void;
  readOnly: boolean;
}

export function EndpointForm({ definition, onChange, readOnly }: EndpointFormProps) {
  const { t } = useTranslation("dashboard");
  const pushToast = useAppStore((s) => s.pushToast);

  const patch = (partial: Partial<EndpointDefinition>) => onChange({ ...definition, ...partial });

  // 媒体类型决定可选素材、产物取值键、可插入的变量与能力字段；视频专属的说明文案在图片定义下换成 `_image` 版本。
  const mediaType = definitionMediaType(definition);
  const isImage = mediaType === "image";
  const profile = MEDIA_TYPE_FORM_PROFILES[mediaType];
  const mediaCopy = (key: string) => t(isImage ? `${key}_image` : key);

  const inputs = useMemo(() => definition.inputs ?? {}, [definition.inputs]);
  const statusMap = definition.status_map ?? {};
  const capabilities = definition.capabilities ?? {};
  const authHeaders = definition.auth?.headers ?? {};
  const authQuery = definition.auth?.query ?? {};
  const noAuth = Object.keys(authHeaders).length === 0 && Object.keys(authQuery).length === 0;

  const submitVariables = useMemo(() => {
    const generation = profile.generationVariables.map(({ name, descKey }) => ({
      token: `{{ ${name} }}`,
      desc: t(descKey),
    }));
    const assets = Object.entries(inputs).map(([name, spec]) => ({
      token: `{{ inputs.${name} }}`,
      desc: t(`ce_input_source_${spec.source}`),
    }));
    return [...generation, ...assets];
  }, [inputs, profile, t]);

  const patchCapabilities = (partial: EndpointCapabilities) =>
    patch({ capabilities: { ...capabilities, ...partial } });

  const setSubmitPaths = (field: "task_id" | "error", paths: EndpointPathItem[]) =>
    patch({
      submit: {
        ...definition.submit,
        extract: {
          ...definition.submit.extract,
          [field]: writePaths(definition.submit.extract[field], paths),
        },
      },
    });

  const setPollPaths = (
    field: "status" | EndpointArtifactKey | "error" | "failure" | "result_id",
    paths: EndpointPathItem[],
  ) =>
    patch({
      poll: {
        ...definition.poll,
        extract: {
          ...definition.poll.extract,
          [field]: writePaths(definition.poll.extract[field], paths),
        },
      },
    });

  const usageSpec = definition.poll.extract.usage?.duration_seconds;
  const setUsagePaths = (paths: EndpointPathItem[]) =>
    patch({
      poll: {
        ...definition.poll,
        extract: {
          ...definition.poll.extract,
          usage: { ...definition.poll.extract.usage, duration_seconds: writePaths(usageSpec, paths) },
        },
      },
    });

  const methodOptions = HTTP_METHODS.map((m) => ({ value: m, label: m }));
  const rename = <T,>(record: Record<string, T>, from: string, to: string, value: T) => {
    const renamed = renameKey(record, from, to, value);
    if (renamed === record && from !== to) pushToast(t("ce_duplicate_key"), "error");
    return renamed;
  };

  const maxReferenceImagesField = (
    <label className="block">
      <span className={LABEL_CLS}>{t("ce_cap_max_reference_images")}</span>
      <Input
        type="number"
        min={0}
        value={capabilities.max_reference_images ?? 0}
        readOnly={readOnly}
        onChange={(e) => patchCapabilities({ max_reference_images: Number(e.target.value) || 0 })}
      />
    </label>
  );

  return (
    <div>
      {/* 1 基本信息 */}
      <FormSection id="meta" step={1} title={t("ce_section_meta")} desc={t("ce_section_meta_desc")}>
        <div className="grid grid-cols-1 gap-3 @md/page:grid-cols-3">
          <TextField
            label={t("ce_meta_name")}
            value={definition.meta.name}
            readOnly={readOnly}
            placeholder={t("ce_meta_name_placeholder")}
            onChange={(name) => patch({ meta: { ...definition.meta, name } })}
          />
          <TextField
            label={t("ce_meta_author")}
            value={definition.meta.author}
            readOnly={readOnly}
            onChange={(author) => patch({ meta: { ...definition.meta, author } })}
          />
          <TextField
            label={t("ce_meta_version")}
            value={definition.meta.version}
            readOnly={readOnly}
            onChange={(version) => patch({ meta: { ...definition.meta, version } })}
          />
        </div>
        <div className="mt-3">
          <TextField
            label={t("ce_meta_base_url")}
            value={definition.meta.hints?.base_url ?? ""}
            readOnly={readOnly}
            spellCheck={false}
            placeholder="https://api.example.com"
            hint={t("ce_meta_base_url_hint")}
            onChange={(base_url) =>
              patch({
                meta: {
                  ...definition.meta,
                  hints: { ...definition.meta.hints, base_url: base_url || undefined },
                },
              })
            }
          />
        </div>
      </FormSection>

      {/* 2 访问凭证 */}
      <FormSection id="auth" step={2} title={t("ce_section_auth")} desc={t("ce_section_auth_desc")}>
        <CheckboxField
          label={t("ce_auth_none")}
          checked={noAuth}
          disabled={readOnly}
          onChange={(checked) =>
            patch({ auth: checked ? {} : { headers: { Authorization: "Bearer {{ api_key }}" } } })
          }
        />
        {noAuth ? (
          <span className={HINT_CLS}>{t("ce_auth_none_hint")}</span>
        ) : (
          <div className="mt-3">
            <span className={LABEL_CLS}>{t("ce_auth_headers")}</span>
            <div className="space-y-2">
              {Object.entries(authHeaders).map(([name, value], index) => (
                // 键即行名，正在被编辑；用它做 key 会让每敲一个字符就重建输入框、丢掉焦点。
                // renameKey 保序，行的位置在重命名中不变，因此下标是这里稳定的行标识。
                <div key={index} className="grid grid-cols-[180px_1fr_32px] items-center gap-3">
                  <div className="min-w-0">
                    <Input
                      type="text"
                      mono
                      value={name}
                      readOnly={readOnly}
                      aria-label={t("ce_auth_header_name")}
                      onChange={(e) =>
                        patch({
                          auth: {
                            ...definition.auth,
                            headers: rename(authHeaders, name, e.target.value, value),
                          },
                        })
                      }
                      autoComplete="off"
                      spellCheck={false}
                    />
                  </div>
                  <TextField
                    ariaLabel={t("ce_auth_header_value")}
                    value={value}
                    readOnly={readOnly}
                    mono
                    insertable
                    onChange={(next) =>
                      patch({
                        auth: { ...definition.auth, headers: { ...authHeaders, [name]: next } },
                      })
                    }
                  />
                  {!readOnly && (
                    <RowDeleteButton
                      label={t("ce_auth_header_remove")}
                      onClick={() => {
                        const next = { ...authHeaders };
                        delete next[name];
                        patch({ auth: { ...definition.auth, headers: next } });
                      }}
                    />
                  )}
                </div>
              ))}
            </div>
            {!readOnly && (
              <AddRowButton
                label={t("ce_auth_header_add")}
                disabled={"" in authHeaders}
                disabledHint={t("ce_row_name_first")}
                onClick={() =>
                  patch({ auth: { ...definition.auth, headers: { ...authHeaders, "": "" } } })
                }
              />
            )}
            <VariableChips
              variables={[{ token: "{{ api_key }}", desc: t("ce_var_api_key") }]}
              note={t("ce_auth_api_key_note")}
            />
          </div>
        )}
      </FormSection>

      {/* 3 输入素材 */}
      <FormSection id="inputs" step={3} title={t("ce_section_inputs")} desc={t("ce_section_inputs_desc")}>
        <div className="space-y-2">
          <div className="grid grid-cols-[1fr_180px_150px_110px_32px] gap-3 text-xs text-muted-foreground">
            <span className="px-1">{t("ce_input_variable")}</span>
            <span className="px-1">{t("ce_input_source")}</span>
            <span className="px-1">{t("ce_input_encoding")}</span>
            <span className="px-1">{t("ce_input_required")}</span>
            <span />
          </div>
          {Object.entries(inputs).map(([name, spec], index) => (
            <div key={index} className="grid grid-cols-[1fr_180px_150px_110px_32px] items-center gap-3">
              <div className="min-w-0">
                <Input
                  type="text"
                  mono
                  value={name}
                  readOnly={readOnly}
                  aria-label={t("ce_input_variable")}
                  onChange={(e) => patch({ inputs: rename(inputs, name, e.target.value, spec) })}
                  autoComplete="off"
                  spellCheck={false}
                />
              </div>
              <SelectField<EndpointInputSource>
                value={spec.source}
                disabled={readOnly}
                ariaLabel={t("ce_input_source")}
                options={profile.inputSources.map((s) => ({ value: s, label: t(`ce_input_source_${s}`) }))}
                onChange={(source) => patch({ inputs: { ...inputs, [name]: { ...spec, source } } })}
              />
              <SelectField<EndpointInputEncoding>
                value={spec.encoding}
                disabled={readOnly}
                ariaLabel={t("ce_input_encoding")}
                options={INPUT_ENCODINGS.map((enc) => ({ value: enc, label: t(`ce_input_encoding_${enc}`) }))}
                onChange={(encoding) => patch({ inputs: { ...inputs, [name]: { ...spec, encoding } } })}
              />
              <CheckboxField
                label={t("ce_input_required")}
                checked={spec.required ?? false}
                disabled={readOnly}
                onChange={(required) =>
                  patch({ inputs: { ...inputs, [name]: { ...spec, required } } })
                }
              />
              {!readOnly && (
                <RowDeleteButton
                  label={t("ce_input_remove")}
                  onClick={() => {
                    const next = { ...inputs };
                    delete next[name];
                    patch({ inputs: next });
                  }}
                />
              )}
            </div>
          ))}
        </div>
        {!readOnly && profile.inputSources.length > 0 && (
          <AddRowButton
            label={t("ce_input_add")}
            disabled={"" in inputs}
            disabledHint={t("ce_row_name_first")}
            onClick={() =>
              patch({
                inputs: { ...inputs, "": { source: profile.inputSources[0], encoding: "data_uri" } },
              })
            }
          />
        )}
        <span className={HINT_CLS}>{t("ce_inputs_hint")}</span>
      </FormSection>

      {/* 4 提交生成任务 */}
      <FormSection id="submit" step={4} title={t("ce_section_submit")} desc={t("ce_section_submit_desc")}>
        <div className="grid grid-cols-[120px_1fr] gap-3">
          <div>
            <span className={LABEL_CLS}>{t("ce_request_method")}</span>
            <SelectField
              value={definition.submit.method}
              disabled={readOnly}
              ariaLabel={t("ce_request_method")}
              options={methodOptions}
              onChange={(method) => patch({ submit: { ...definition.submit, method } })}
            />
          </div>
          <TextField
            label={t("ce_submit_url")}
            value={definition.submit.url}
            readOnly={readOnly}
            mono
            insertable
            hint={t("ce_base_url_hint")}
            onChange={(url) => patch({ submit: { ...definition.submit, url } })}
          />
        </div>
        <div className="mt-3">
          <span className={LABEL_CLS}>{t("ce_submit_body")}</span>
          <JsonBodyEditor
            value={definition.submit.body}
            readOnly={readOnly}
            ariaLabel={t("ce_submit_body")}
            onChange={(body) => patch({ submit: { ...definition.submit, body } })}
          />
          <VariableChips variables={submitVariables} note={t("ce_submit_variables_note")} />
        </div>
        <div className="mt-3 space-y-3">
          <PathsEditor
            label={t("ce_submit_task_id")}
            paths={readPaths(definition.submit.extract.task_id)}
            readOnly={readOnly}
            hint={t("ce_paths_hint")}
            onChange={(paths) => setSubmitPaths("task_id", paths)}
          />
          <PathsEditor
            label={t("ce_submit_error")}
            paths={readPaths(definition.submit.extract.error)}
            readOnly={readOnly}
            onChange={(paths) => setSubmitPaths("error", paths)}
          />
        </div>
      </FormSection>

      {/* 5 查询进度与结果 */}
      <FormSection id="poll" step={5} title={t("ce_section_poll")} desc={mediaCopy("ce_section_poll_desc")}>
        <div className="grid grid-cols-[120px_1fr] gap-3">
          <div>
            <span className={LABEL_CLS}>{t("ce_request_method")}</span>
            <SelectField
              value={definition.poll.method}
              disabled={readOnly}
              ariaLabel={t("ce_request_method")}
              options={methodOptions}
              onChange={(method) => patch({ poll: { ...definition.poll, method } })}
            />
          </div>
          <TextField
            label={t("ce_poll_url")}
            value={definition.poll.url}
            readOnly={readOnly}
            mono
            insertable
            hint={t("ce_poll_url_hint")}
            onChange={(url) => patch({ poll: { ...definition.poll, url } })}
          />
        </div>
        <div className="mt-3 space-y-3">
          <PathsEditor
            label={t("ce_poll_status")}
            paths={readPaths(definition.poll.extract.status)}
            readOnly={readOnly}
            onChange={(paths) => setPollPaths("status", paths)}
          />
          {profile.artifactKeys.map((key) => (
            <PathsEditor
              key={key}
              label={t(`ce_poll_${key}`)}
              paths={readPaths(definition.poll.extract[key])}
              readOnly={readOnly}
              onChange={(paths) => setPollPaths(key, paths)}
            />
          ))}
          <PathsEditor
            label={t("ce_poll_error")}
            paths={readPaths(definition.poll.extract.error)}
            readOnly={readOnly}
            onChange={(paths) => setPollPaths("error", paths)}
          />
          {!isImage && (
            <PathsEditor
              label={t("ce_poll_usage")}
              paths={readPaths(usageSpec)}
              readOnly={readOnly}
              hint={t("ce_poll_usage_hint")}
              onChange={setUsagePaths}
            />
          )}
        </div>
        <div className="mt-4 border-t border-border pt-3.5">
          <CheckboxField
            label={t("ce_result_enabled")}
            checked={definition.result !== undefined}
            disabled={readOnly}
            onChange={(checked) =>
              patch({
                result: checked
                  ? { method: "GET", url: "{{ base_url }}/", extract: { [profile.artifactKeys[0]]: [] } }
                  : undefined,
              })
            }
          />
          <span className={HINT_CLS}>{mediaCopy("ce_result_hint")}</span>
          {definition.result && (
            <div className="mt-3 flex flex-col gap-3 rounded-lg border border-border p-3.5">
              <TextField
                label={t("ce_result_url")}
                value={definition.result.url}
                readOnly={readOnly}
                mono
                insertable
                onChange={(url) =>
                  definition.result && patch({ result: { ...definition.result, url } })
                }
              />
              {profile.artifactKeys.map((key) => (
                <PathsEditor
                  key={key}
                  label={t(`ce_result_${key}`)}
                  paths={readPaths(definition.result?.extract[key])}
                  readOnly={readOnly}
                  onChange={(paths) =>
                    definition.result &&
                    patch({
                      result: {
                        ...definition.result,
                        extract: {
                          ...definition.result.extract,
                          [key]: writePaths(definition.result.extract[key], paths),
                        },
                      },
                    })
                  }
                />
              ))}
              <PathsEditor
                label={t("ce_poll_result_id")}
                paths={readPaths(definition.poll.extract.result_id)}
                readOnly={readOnly}
                hint={t("ce_result_id_hint")}
                onChange={(paths) => setPollPaths("result_id", paths)}
              />
            </div>
          )}
          <span className={HINT_CLS}>{mediaCopy("ce_download_policy")}</span>
        </div>
      </FormSection>

      {/* 6 状态对照 */}
      <FormSection id="status" step={6} title={t("ce_section_status")} desc={t("ce_section_status_desc")}>
        <div className="space-y-2">
          <div className="grid grid-cols-[1fr_16px_180px_32px] gap-3 text-xs text-muted-foreground">
            <span className="px-1">{t("ce_status_provider_value")}</span>
            <span />
            <span className="px-1">{t("ce_status_standard")}</span>
            <span />
          </div>
          {Object.entries(statusMap).map(([from, to], index) => (
            <div key={index} className="grid grid-cols-[1fr_16px_180px_32px] items-center gap-3">
              <div className="min-w-0">
                <Input
                  type="text"
                  mono
                  value={from}
                  readOnly={readOnly}
                  aria-label={t("ce_status_provider_value")}
                  onChange={(e) => patch({ status_map: rename(statusMap, from, e.target.value, to) })}
                  autoComplete="off"
                  spellCheck={false}
                />
              </div>
              <span aria-hidden className="text-center text-muted-foreground">
                →
              </span>
              <SelectField<EndpointStandardStatus>
                value={to}
                disabled={readOnly}
                ariaLabel={t("ce_status_standard")}
                options={STANDARD_STATUSES.map((s) => ({ value: s, label: t(`ce_status_${s}`) }))}
                onChange={(next) => patch({ status_map: { ...statusMap, [from]: next } })}
              />
              {!readOnly && (
                <RowDeleteButton
                  label={t("ce_status_remove")}
                  onClick={() => {
                    const next = { ...statusMap };
                    delete next[from];
                    patch({ status_map: next });
                  }}
                />
              )}
            </div>
          ))}
        </div>
        {!readOnly && (
          <AddRowButton
            label={t("ce_status_add")}
            disabled={"" in statusMap}
            disabledHint={t("ce_row_name_first")}
            onClick={() => patch({ status_map: { ...statusMap, "": "running" } })}
          />
        )}
      </FormSection>

      {/* 7 支持的功能 */}
      <FormSection
        id="capabilities"
        step={7}
        title={t("ce_section_capabilities")}
        desc={t("ce_section_capabilities_desc")}
      >
        {isImage ? (
          <>
            <div className="flex flex-wrap gap-x-6 gap-y-2.5">
              <CheckboxField
                label={t("ce_cap_text_to_image")}
                checked={capabilities.text_to_image ?? false}
                disabled={readOnly}
                onChange={(text_to_image) => patchCapabilities({ text_to_image })}
              />
              <CheckboxField
                label={t("ce_cap_image_to_image")}
                checked={capabilities.image_to_image ?? false}
                disabled={readOnly}
                onChange={(image_to_image) => patchCapabilities({ image_to_image })}
              />
            </div>
            <div className="mt-3 grid grid-cols-1 gap-3 @md/page:grid-cols-3">{maxReferenceImagesField}</div>
          </>
        ) : (
          <>
          <div className="flex flex-wrap gap-x-6 gap-y-2.5">
            <CheckboxField
              label={t("ce_cap_text_to_video")}
              checked={capabilities.text_to_video ?? true}
              disabled={readOnly}
              onChange={(text_to_video) => patchCapabilities({ text_to_video })}
            />
            <CheckboxField
              label={t("ce_cap_first_frame")}
              checked={capabilities.first_frame ?? false}
              disabled={readOnly}
              onChange={(first_frame) => patchCapabilities({ first_frame })}
            />
            <CheckboxField
              label={t("ce_cap_last_frame")}
              checked={capabilities.last_frame ?? false}
              disabled={readOnly}
              onChange={(last_frame) => patchCapabilities({ last_frame })}
            />
          </div>
          <div className="mt-3 grid grid-cols-1 gap-3 @md/page:grid-cols-3">
            {maxReferenceImagesField}
            <div>
              <span className={LABEL_CLS}>{t("ce_cap_reference_audio_mode")}</span>
              <SelectField<"none" | "direct">
                value={capabilities.reference_audio_mode ?? "none"}
                disabled={readOnly}
                ariaLabel={t("ce_cap_reference_audio_mode")}
                options={[
                  { value: "none", label: t("ce_cap_audio_mode_none") },
                  { value: "direct", label: t("ce_cap_audio_mode_direct") },
                ]}
                onChange={(reference_audio_mode) => patchCapabilities({ reference_audio_mode })}
              />
            </div>
            <label className="block">
              <span className={LABEL_CLS}>{t("ce_cap_max_reference_audio_count")}</span>
              <Input
                type="number"
                min={0}
                value={capabilities.max_reference_audio_count ?? 0}
                readOnly={readOnly}
                onChange={(e) =>
                  patchCapabilities({ max_reference_audio_count: Number(e.target.value) || 0 })
                }
              />
            </label>
          </div>
          </>
        )}
        <span className={HINT_CLS}>{mediaCopy("ce_capabilities_hint")}</span>
      </FormSection>
    </div>
  );
}
