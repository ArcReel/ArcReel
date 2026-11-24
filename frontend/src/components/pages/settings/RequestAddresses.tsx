import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { cn } from "cn";
import { TruncatedText } from "@/components/shared/TruncatedText";
import { Button } from "@/components/ui/button";
import { InputGroup, InputGroupAddon, InputGroupInput } from "@/components/ui/input-group";
import { InfoTip, InfoTipContent, InfoTipTrigger } from "@/components/ui/info-tip";
import type { EndpointRequest, PathOverrides } from "@/types";

const MODEL_PLACEHOLDER = /\{\{\s*model\s*\}\}/g;
const VERSION_SUFFIX = /\/v\d+(?:\.\d+)?[a-zA-Z]*$/;
const ENSURED_SUFFIX: Partial<Record<EndpointRequest["base_url_rule"], string>> = {
  ensure_api_v3: "/api/v3",
  ensure_ent_v2: "/ent/v2",
};
const DASHSCOPE_SUFFIX = /\/(?:compatible-mode\/v1|api\/v1)$/;
const MESSAGES_SUFFIX = /\/+v1\/messages$/;

function rawBaseUrl(baseUrl: string): string {
  const stripped = baseUrl.trim().replace(/\/+$/, "");
  return stripped && !stripped.includes("://") ? `https://${stripped}` : stripped;
}

function normalizedBase(raw: string, request: EndpointRequest) {
  if (request.base_url_rule === "strip_version") return { base: raw.replace(VERSION_SUFFIX, ""), autoV1: false };
  if (request.base_url_rule === "append_v1") return { base: raw, autoV1: !/\/v\d+$/.test(raw) };
  if (request.base_url_rule === "ensure_v1") return { base: raw, autoV1: !raw.endsWith("/v1") };
  if (request.base_url_rule === "dashscope_api_v1")
    return { base: `${raw.replace(DASHSCOPE_SUFFIX, "")}/api/v1`, autoV1: false };
  if (request.base_url_rule === "strip_messages") return { base: raw.replace(MESSAGES_SUFFIX, ""), autoV1: false };
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

export function RequestAddresses({ requests, baseUrl, modelId, overrides, onChange }: RequestAddressesProps) {
  const { t } = useTranslation("dashboard");
  const raw = rawBaseUrl(baseUrl);
  const showPath = (path: string) => (modelId ? path.replace(MODEL_PLACEHOLDER, modelId) : path);

  const commit = (request: EndpointRequest, draft: string) => {
    const trimmed = draft.trim();
    const path = trimmed && !trimmed.startsWith("/") ? `/${trimmed}` : trimmed;
    const { [request.name]: _previous, ...rest } = overrides ?? {};
    const { base, autoV1 } = normalizedBase(raw, request);
    const unchanged = !path || (path === request.default_path && base === raw && !autoV1);
    const next = unchanged ? rest : { ...rest, [request.name]: path };
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
  display: string;
  onCommit: (draft: string) => void;
}

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
          onBlur={() => finish(draft !== (override ?? request.default_path))}
          onKeyDown={(e) => {
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
        <InfoTip>
          <InfoTipTrigger
            render={
              <button
                type="button"
                className="mx-0.5 shrink-0 rounded-sm border border-dashed border-border px-0.5 text-muted-foreground"
              />
            }
          >
            /v1
          </InfoTipTrigger>
          <InfoTipContent title="/v1">{t("cp_request_auto_v1")}</InfoTipContent>
        </InfoTip>
      )}
      <button
        type="button"
        aria-label={`${label} ${display}`}
        className="min-w-0 flex-1 truncate rounded-sm text-left text-foreground hover:bg-accent focus-ring"
        onClick={() => setDraft(override ?? request.default_path)}
      >
        {display}
      </button>
    </div>
  );
}
