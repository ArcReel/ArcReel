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
 * 新建记忆文件：输入文件名后立即创建（与保存同一个 PUT，正文是带 frontmatter 的模板），成功后选中新文件。
 * 文件名是创建动作的参数，不是未保存修改，不登记离开拦截。
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
  /** 目录里一个文件都没有：先说明记忆从哪里来。 */
  empty: boolean;
  /** 记忆层级的说明；所在页面已有说明时不传。 */
  intro?: string;
  heading: "h2" | "h3";
  /**
   * 创建成功后选中新文件；返回 Promise 时等它完成再结束提交态。
   * `signal` 在表单卸载（已切到别的文件或离开本页）时中止，此时不再跳到新文件。
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
