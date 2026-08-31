import { useMemo } from "react";
import { useTranslation } from "react-i18next";
import { INPUT_CLS } from "@/components/ui/darkroom-tokens";
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
  HTTP_METHODS,
  INPUT_ENCODINGS,
  INPUT_SOURCES,
  STANDARD_STATUSES,
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
  MONO_INPUT_CLS,
  PathsEditor,
  RowDeleteButton,
  TextField,
  VariableChips,
} from "./endpoint-form-primitives";
import { JsonBodyEditor } from "./JsonBodyEditor";

interface EndpointFormProps {
  definition: EndpointDefinition;
  onChange: (next: EndpointDefinition) => void;
  readOnly: boolean;
}

/** 生成参数变量。素材变量按 inputs 声明动态派生，声明前不出现。 */
const GENERATION_VARIABLES = [
  "prompt",
  "model",
  "duration",
  "duration_seconds",
  "resolution",
  "aspect_ratio",
  "width",
  "height",
  "generate_audio",
] as const;

export function EndpointForm({ definition, onChange, readOnly }: EndpointFormProps) {
  const { t } = useTranslation("dashboard");
  const pushToast = useAppStore((s) => s.pushToast);

  const patch = (partial: Partial<EndpointDefinition>) => onChange({ ...definition, ...partial });

  const inputs = useMemo(() => definition.inputs ?? {}, [definition.inputs]);
  const statusMap = definition.status_map ?? {};
  const capabilities = definition.capabilities ?? {};
  const authHeaders = definition.auth?.headers ?? {};
  const authQuery = definition.auth?.query ?? {};
  const noAuth = Object.keys(authHeaders).length === 0 && Object.keys(authQuery).length === 0;

  const submitVariables = useMemo(() => {
    const generation = GENERATION_VARIABLES.map((name) => ({
      token: `{{ ${name} }}`,
      desc: t(`ce_var_${name}`),
    }));
    const assets = Object.entries(inputs).map(([name, spec]) => ({
      token: `{{ inputs.${name} }}`,
      desc: t(`ce_input_source_${spec.source}`),
    }));
    return [...generation, ...assets];
  }, [inputs, t]);

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
    field: "status" | "video_url" | "error" | "failure" | "result_id",
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

  const selectCls = `${INPUT_CLS} disabled:cursor-not-allowed`;
  const rename = <T,>(record: Record<string, T>, from: string, to: string, value: T) => {
    const renamed = renameKey(record, from, to, value);
    if (renamed === record && from !== to) pushToast(t("ce_duplicate_key"), "error");
    return renamed;
  };

  return (
    <div
      className={
        readOnly
          ? "[&_input:read-only]:border-accent/25 [&_input:read-only]:bg-bg-grad-b/65 [&_input:read-only]:text-text-2 [&_select:disabled]:border-accent/20 [&_select:disabled]:bg-bg-grad-b/55 [&_select:disabled]:text-text-2 [&_textarea:read-only]:border-accent/25 [&_textarea:read-only]:bg-bg-grad-b/65 [&_textarea:read-only]:text-text-2"
          : undefined
      }
    >
      {/* 1 基本信息 */}
      <FormSection id="meta" step={1} title={t("ce_section_meta")} desc={t("ce_section_meta_desc")}>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
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
            mono
            onChange={(version) => patch({ meta: { ...definition.meta, version } })}
          />
        </div>
        <div className="mt-3">
          <TextField
            label={t("ce_meta_base_url")}
            value={definition.meta.hints?.base_url ?? ""}
            readOnly={readOnly}
            mono
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
                  <input
                    type="text"
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
                    className={`${INPUT_CLS} ${MONO_INPUT_CLS}`}
                  />
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
          <div className="grid grid-cols-[1fr_180px_150px_110px_32px] gap-3 text-[11.5px] text-text-3">
            <span className="px-1">{t("ce_input_variable")}</span>
            <span className="px-1">{t("ce_input_source")}</span>
            <span className="px-1">{t("ce_input_encoding")}</span>
            <span className="px-1">{t("ce_input_required")}</span>
            <span />
          </div>
          {Object.entries(inputs).map(([name, spec], index) => (
            <div key={index} className="grid grid-cols-[1fr_180px_150px_110px_32px] items-center gap-3">
              <input
                type="text"
                value={name}
                readOnly={readOnly}
                aria-label={t("ce_input_variable")}
                onChange={(e) => patch({ inputs: rename(inputs, name, e.target.value, spec) })}
                className={`${INPUT_CLS} ${MONO_INPUT_CLS}`}
              />
              <select
                value={spec.source}
                disabled={readOnly}
                aria-label={t("ce_input_source")}
                onChange={(e) =>
                  patch({
                    inputs: { ...inputs, [name]: { ...spec, source: e.target.value as EndpointInputSource } },
                  })
                }
                className={selectCls}
              >
                {INPUT_SOURCES.map((s) => (
                  <option key={s} value={s}>
                    {t(`ce_input_source_${s}`)}
                  </option>
                ))}
              </select>
              <select
                value={spec.encoding}
                disabled={readOnly}
                aria-label={t("ce_input_encoding")}
                onChange={(e) =>
                  patch({
                    inputs: {
                      ...inputs,
                      [name]: { ...spec, encoding: e.target.value as EndpointInputEncoding },
                    },
                  })
                }
                className={selectCls}
              >
                {INPUT_ENCODINGS.map((enc) => (
                  <option key={enc} value={enc}>
                    {t(`ce_input_encoding_${enc}`)}
                  </option>
                ))}
              </select>
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
        {!readOnly && (
          <AddRowButton
            label={t("ce_input_add")}
            disabled={"" in inputs}
            disabledHint={t("ce_row_name_first")}
            onClick={() =>
              patch({
                inputs: { ...inputs, "": { source: "start_image", encoding: "data_uri" } },
              })
            }
          />
        )}
        <span className={HINT_CLS}>{t("ce_inputs_hint")}</span>
      </FormSection>

      {/* 4 提交生成任务 */}
      <FormSection id="submit" step={4} title={t("ce_section_submit")} desc={t("ce_section_submit_desc")}>
        <div className="grid grid-cols-[120px_1fr] gap-3">
          <label className="block">
            <span className={LABEL_CLS}>{t("ce_request_method")}</span>
            <select
              value={definition.submit.method}
              disabled={readOnly}
              onChange={(e) => patch({ submit: { ...definition.submit, method: e.target.value } })}
              className={selectCls}
            >
              {HTTP_METHODS.map((m) => (
                <option key={m} value={m}>
                  {m}
                </option>
              ))}
            </select>
          </label>
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
      <FormSection id="poll" step={5} title={t("ce_section_poll")} desc={t("ce_section_poll_desc")}>
        <div className="grid grid-cols-[120px_1fr] gap-3">
          <label className="block">
            <span className={LABEL_CLS}>{t("ce_request_method")}</span>
            <select
              value={definition.poll.method}
              disabled={readOnly}
              onChange={(e) => patch({ poll: { ...definition.poll, method: e.target.value } })}
              className={selectCls}
            >
              {HTTP_METHODS.map((m) => (
                <option key={m} value={m}>
                  {m}
                </option>
              ))}
            </select>
          </label>
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
          <PathsEditor
            label={t("ce_poll_video_url")}
            paths={readPaths(definition.poll.extract.video_url)}
            readOnly={readOnly}
            onChange={(paths) => setPollPaths("video_url", paths)}
          />
          <PathsEditor
            label={t("ce_poll_error")}
            paths={readPaths(definition.poll.extract.error)}
            readOnly={readOnly}
            onChange={(paths) => setPollPaths("error", paths)}
          />
          <PathsEditor
            label={t("ce_poll_usage")}
            paths={readPaths(usageSpec)}
            readOnly={readOnly}
            hint={t("ce_poll_usage_hint")}
            onChange={setUsagePaths}
          />
        </div>
        <div className="mt-4 border-t border-hairline-soft pt-3.5">
          <CheckboxField
            label={t("ce_result_enabled")}
            checked={definition.result !== undefined}
            disabled={readOnly}
            onChange={(checked) =>
              patch({
                result: checked
                  ? { method: "GET", url: "{{ base_url }}/", extract: { video_url: [] } }
                  : undefined,
              })
            }
          />
          <span className={HINT_CLS}>{t("ce_result_hint")}</span>
          {definition.result && (
            <div className="mt-3 space-y-3 rounded-[8px] border border-hairline-soft bg-bg-grad-a/30 p-3.5">
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
              <PathsEditor
                label={t("ce_result_video_url")}
                paths={readPaths(definition.result.extract.video_url)}
                readOnly={readOnly}
                onChange={(paths) =>
                  definition.result &&
                  patch({
                    result: {
                      ...definition.result,
                      extract: {
                        ...definition.result.extract,
                        video_url: writePaths(definition.result.extract.video_url, paths),
                      },
                    },
                  })
                }
              />
              <PathsEditor
                label={t("ce_poll_result_id")}
                paths={readPaths(definition.poll.extract.result_id)}
                readOnly={readOnly}
                hint={t("ce_result_id_hint")}
                onChange={(paths) => setPollPaths("result_id", paths)}
              />
            </div>
          )}
          <span className={HINT_CLS}>{t("ce_download_policy")}</span>
        </div>
      </FormSection>

      {/* 6 状态对照 */}
      <FormSection id="status" step={6} title={t("ce_section_status")} desc={t("ce_section_status_desc")}>
        <div className="space-y-2">
          <div className="grid grid-cols-[1fr_16px_180px_32px] gap-3 text-[11.5px] text-text-3">
            <span className="px-1">{t("ce_status_provider_value")}</span>
            <span />
            <span className="px-1">{t("ce_status_standard")}</span>
            <span />
          </div>
          {Object.entries(statusMap).map(([from, to], index) => (
            <div key={index} className="grid grid-cols-[1fr_16px_180px_32px] items-center gap-3">
              <input
                type="text"
                value={from}
                readOnly={readOnly}
                aria-label={t("ce_status_provider_value")}
                onChange={(e) => patch({ status_map: rename(statusMap, from, e.target.value, to) })}
                className={`${INPUT_CLS} ${MONO_INPUT_CLS}`}
              />
              <span aria-hidden className="text-center text-text-3">
                →
              </span>
              <select
                value={to}
                disabled={readOnly}
                aria-label={t("ce_status_standard")}
                onChange={(e) =>
                  patch({
                    status_map: { ...statusMap, [from]: e.target.value as EndpointStandardStatus },
                  })
                }
                className={selectCls}
              >
                {STANDARD_STATUSES.map((s) => (
                  <option key={s} value={s}>
                    {t(`ce_status_${s}`)}
                  </option>
                ))}
              </select>
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
        <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-3">
          <label className="block">
            <span className={LABEL_CLS}>{t("ce_cap_max_reference_images")}</span>
            <input
              type="number"
              min={0}
              value={capabilities.max_reference_images ?? 0}
              readOnly={readOnly}
              onChange={(e) =>
                patchCapabilities({ max_reference_images: Number(e.target.value) || 0 })
              }
              className={INPUT_CLS}
            />
          </label>
          <label className="block">
            <span className={LABEL_CLS}>{t("ce_cap_reference_audio_mode")}</span>
            <select
              value={capabilities.reference_audio_mode ?? "none"}
              disabled={readOnly}
              onChange={(e) =>
                patchCapabilities({ reference_audio_mode: e.target.value as "none" | "direct" })
              }
              className={selectCls}
            >
              <option value="none">{t("ce_cap_audio_mode_none")}</option>
              <option value="direct">{t("ce_cap_audio_mode_direct")}</option>
            </select>
          </label>
          <label className="block">
            <span className={LABEL_CLS}>{t("ce_cap_max_reference_audio_count")}</span>
            <input
              type="number"
              min={0}
              value={capabilities.max_reference_audio_count ?? 0}
              readOnly={readOnly}
              onChange={(e) =>
                patchCapabilities({ max_reference_audio_count: Number(e.target.value) || 0 })
              }
              className={INPUT_CLS}
            />
          </label>
        </div>
        <span className={HINT_CLS}>{t("ce_capabilities_hint")}</span>
      </FormSection>
    </div>
  );
}
