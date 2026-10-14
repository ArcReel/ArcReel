import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { cn } from "cn";
import { TruncatedText } from "@/components/shared/TruncatedText";
import { Button } from "@/components/ui/button";
import { InputGroup, InputGroupAddon, InputGroupInput } from "@/components/ui/input-group";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import type { EndpointRequest, PathOverrides } from "@/types";

const MODEL_PLACEHOLDER = /\{\{\s*model\s*\}\}/g;
const VERSION_SUFFIX = /\/v\d+(?:\.\d+)?[a-zA-Z]*$/;
const ENSURED_SUFFIX: Partial<Record<EndpointRequest["base_url_rule"], string>> = {
  ensure_api_v3: "/api/v3",
  ensure_ent_v2: "/ent/v2",
};
const DASHSCOPE_SUFFIX = /\/(?:compatible-mode\/v1|api\/v1)$/;

/** 供应商填写的 base_url 原样值（补缺失的协议、去尾斜杠），与服务端 raw_base_url 同口径。 */
function rawBaseUrl(baseUrl: string): string {
  const stripped = baseUrl.trim().replace(/\/+$/, "");
  return stripped && !stripped.includes("://") ? `https://${stripped}` : stripped;
}

/** 没有修改时端点按声明的规则归一化接口地址；有修改的请求一律相对原样值。 */
function normalizedBase(raw: string, request: EndpointRequest) {
  if (request.base_url_rule === "strip_version") return { base: raw.replace(VERSION_SUFFIX, ""), autoV1: false };
  if (request.base_url_rule === "append_v1") return { base: raw, autoV1: !/\/v\d+$/.test(raw) };
  if (request.base_url_rule === "ensure_v1") return { base: raw, autoV1: !raw.endsWith("/v1") };
  if (request.base_url_rule === "dashscope_api_v1")
    return { base: `${raw.replace(DASHSCOPE_SUFFIX, "")}/api/v1`, autoV1: false };
  const suffix = ENSURED_SUFFIX[request.base_url_rule];
  if (suffix) return { base: raw.endsWith(suffix) ? raw : raw + suffix, autoV1: false };
  return { base: raw, autoV1: false };
}

interface RequestAddressesProps {
  requests: EndpointRequest[];
  baseUrl: string;
  modelId: string;
  overrides: PathOverrides | null;
  onChange: (next: PathOverrides | null) => void;
}

/** 调用端点的每条请求一条分段地址条：接口地址灰显，路径部分点击就地修改。 */
export function RequestAddresses({ requests, baseUrl, modelId, overrides, onChange }: RequestAddressesProps) {
  const { t } = useTranslation("dashboard");
  const raw = rawBaseUrl(baseUrl);
  const showPath = (path: string) => (modelId ? path.replace(MODEL_PLACEHOLDER, modelId) : path);

  const commit = (request: EndpointRequest, draft: string) => {
    const trimmed = draft.trim();
    const path = trimmed && !trimmed.startsWith("/") ? `/${trimmed}` : trimmed;
    const { [request.name]: _previous, ...rest } = overrides ?? {};
    // 空值或与默认相同的值不算修改
    const next = path && path !== request.default_path ? { ...rest, [request.name]: path } : rest;
    onChange(Object.keys(next).length ? next : null);
  };

  return (
    <div className="flex flex-col gap-3">
      {requests.map((request) => {
        const override = overrides?.[request.name];
        return (
          <div key={request.name} className="flex min-w-0 flex-col gap-1">
            {requests.length > 1 && <span className="text-xs text-muted-foreground">{t(request.display_name_key)}</span>}
            <AddressBar
              request={request}
              rawBase={raw}
              override={override}
              display={showPath(override ?? request.default_path)}
              onCommit={(draft) => commit(request, draft)}
            />
            {override !== undefined && (
              <div className="flex flex-wrap items-center gap-x-2 text-xs text-muted-foreground">
                <span className="min-w-0 font-mono wrap-anywhere">
                  {t("cp_request_default", { path: showPath(request.default_path) })}
                </span>
                <Button variant="link" size="xs" onClick={() => commit(request, "")}>
                  {t("cp_request_reset")}
                </Button>
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

interface AddressBarProps {
  request: EndpointRequest;
  rawBase: string;
  override: string | undefined;
  /** 展示用路径，模型名占位符已换成模型 ID。 */
  display: string;
  onCommit: (draft: string) => void;
}

/** 一条请求的地址条：点击路径进入行内输入，Enter 或失焦提交，Esc 取消。 */
function AddressBar({ request, rawBase, override, display, onCommit }: AddressBarProps) {
  const { t } = useTranslation("dashboard");
  const [draft, setDraft] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const editing = draft !== null;
  const label = t("cp_request_path_label");

  useEffect(() => {
    if (editing) {
      inputRef.current?.focus();
      inputRef.current?.select();
    }
  }, [editing]);

  const finish = (save: boolean) => {
    if (draft === null) return;
    setDraft(null);
    if (save) onCommit(draft);
  };

  if (editing) {
    // 修改后的路径相对原样接口地址，编辑时前缀即原样值
    return (
      <InputGroup>
        <InputGroupAddon className="max-w-3/5">
          <TruncatedText text={rawBase} className="font-mono" />
        </InputGroupAddon>
        <InputGroupInput
          ref={inputRef}
          mono
          aria-label={label}
          value={draft}
          spellCheck={false}
          autoComplete="off"
          onChange={(e) => setDraft(e.target.value)}
          onBlur={() => finish(true)}
          onKeyDown={(e) => {
            // 输入法组合输入中按 Enter 是在确认候选词，不应提交
            if (e.nativeEvent.isComposing) return;
            if (e.key === "Enter") {
              e.preventDefault();
              finish(true);
            } else if (e.key === "Escape") {
              e.preventDefault();
              e.stopPropagation();
              finish(false);
            }
          }}
        />
      </InputGroup>
    );
  }

  const { base, autoV1 } = override === undefined ? normalizedBase(rawBase, request) : { base: rawBase, autoV1: false };
  return (
    <div
      className={cn(
        "flex h-8 min-w-0 items-center rounded-lg border px-2.5 font-mono text-sm",
        override === undefined ? "border-input" : "border-primary/40 bg-primary/10",
      )}
    >
      <TruncatedText text={base} className="max-w-3/5 shrink-0 text-muted-foreground" />
      {autoV1 && (
        <Tooltip>
          <TooltipTrigger
            render={
              <span
                // eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex -- 悬停或聚焦都要能看到说明；它没有可执行的动作
                tabIndex={0}
                className="mx-0.5 shrink-0 rounded-sm border border-dashed border-border px-0.5 text-muted-foreground"
              />
            }
          >
            /v1
          </TooltipTrigger>
          <TooltipContent>{t("cp_request_auto_v1")}</TooltipContent>
        </Tooltip>
      )}
      <button
        type="button"
        aria-label={`${label} ${display}`}
        className="min-w-0 flex-1 truncate rounded-sm text-left text-foreground hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
        onClick={() => setDraft(override ?? request.default_path)}
      >
        {display}
      </button>
    </div>
  );
}
