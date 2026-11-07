import { useEffect, useId, useRef, useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { Loader2, Plus } from "lucide-react";

import { API } from "@/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import type { AgentMemoryScope } from "@/types/agent-memory";
import { errMsg } from "@/utils/async";

import { checkNewName, newFileTemplate, type MemoryEntry } from "./memory-files";

/**
 * Create a memory file immediately after entering its filename, using the save PUT with a frontmatter template body; select the new file on success.
 * The filename is a creation argument rather than unsaved content, so do not register a navigation guard.
 */
export function MemoryCreateForm({
  scope,
  entries,
  empty,
  intro,
  heading: Heading,
  onCreated,
}: {
  scope: AgentMemoryScope;
  entries: MemoryEntry[];
  /** Empty directory: explain where memory comes from first. */
  empty: boolean;
  /** Memory-scope explanation; omit if the page already explains it. */
  intro?: string;
  heading: "h2" | "h3";
  /**
   * Select the new file on successful creation; if this returns a Promise, await it before ending submission state.
   * Abort `signal` on unmount after file switching/navigation; do not navigate to the new file afterward.
   */
  onCreated: (name: string, signal: AbortSignal) => Promise<void> | void;
}) {
  const { t } = useTranslation("dashboard");
  const inputId = useId();
  const errorId = useId();
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const mountedRef = useRef<AbortController | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    mountedRef.current = controller;
    return () => controller.abort();
  }, []);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (creating) return;
    const trimmed = name.trim();
    const problem = checkNewName(trimmed, entries);
    if (problem) {
      setError(t(problem === "invalid" ? "agent_memory_name_invalid" : "agent_memory_name_duplicate"));
      return;
    }
    const signal = mountedRef.current?.signal ?? AbortSignal.abort();
    setCreating(true);
    setError(null);
    try {
      const template = newFileTemplate(
        trimmed,
        scope.level,
        t("agent_memory_template_description"),
        t("agent_memory_template_body"),
      );
      await API.saveAgentMemoryFile(scope, trimmed, template);
      await onCreated(trimmed, signal);
    } catch (err) {
      setError(t("agent_memory_action_failed", { message: errMsg(err) }));
    } finally {
      setCreating(false);
    }
  };

  return (
    <form className="flex max-w-120 flex-col gap-4" onSubmit={(event) => void submit(event)} noValidate>
      <div className="flex flex-col gap-1">
        <Heading className="text-base font-medium">{t("agent_memory_new_file")}</Heading>
        {intro && <p className="max-w-[40em] text-sm text-muted-foreground">{intro}</p>}
        {empty && <p className="max-w-[40em] text-sm text-muted-foreground">{t("agent_memory_empty_hint")}</p>}
      </div>
      <div className="flex flex-col gap-2">
        <Label htmlFor={inputId}>{t("agent_memory_new_file_name")}</Label>
        <Input
          id={inputId}
          value={name}
          placeholder={t("agent_memory_new_file_placeholder")}
          autoComplete="off"
          spellCheck={false}
          aria-invalid={error !== null}
          aria-describedby={error !== null ? errorId : undefined}
          disabled={creating}
          onChange={(event) => {
            setName(event.target.value);
            setError(null);
          }}
        />
        {error !== null && (
          <p id={errorId} role="alert" className="text-sm text-destructive">
            {error}
          </p>
        )}
      </div>
      <div>
        <Button type="submit" disabled={creating}>
          {creating ? (
            <Loader2 aria-hidden data-icon="inline-start" className="animate-spin" />
          ) : (
            <Plus aria-hidden data-icon="inline-start" />
          )}
          {t("agent_memory_create")}
        </Button>
      </div>
    </form>
  );
}
