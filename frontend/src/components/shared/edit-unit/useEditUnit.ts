import { useCallback, useContext, useEffect, useState } from "react";

import { errMsg } from "@/utils/async";

import { EditUnitRetentionContext, useRetainWhile } from "./edit-unit-retention";

import { useLeaveGuard } from "./LeaveGuard";

/** Save states: Saved briefly displays before returning to idle. */
export type SaveStatus = "idle" | "saving" | "saved" | "error";

/** Duration of Saved displays in save bars/notices. */
const SAVED_DISPLAY_MS = 2000;

export interface EditUnitOptions<T> {
  /**
   * Saved contents: loaded results and subsequent SSE/Agent-rewrite updates.
   * New values apply directly without unsaved edits; otherwise retain edits and mark external updates.
   */
  source: T;
  /**
   * Submit unsaved edits. `savedValue` is pre-submission saved content for field-incremental PATCH generation.
   * Return server-saved contents; omitted returns use submitted contents. Errors mean failed saves and appear in save bars/notices.
   * Throw `PartialSaveError` for multistep saves with earlier persisted steps. Stable references (`useCallback`) required.
   */
  save: (value: T, savedValue: T) => Promise<T | void>;
  /** Content equality, default JSON-serialization comparison; stable references or module functions required. */
  isEqual?: (a: T, b: T) => boolean;
  /** Leave-dialog titles, e.g. Storyboard 3 has unsaved changes; defaults to generic titles. */
  leaveTitle?: string;
  /**
   * Allow in-app navigation returning true when it does not unmount this edit unit (e.g. tabs within one unit).
   * Arguments are target paths including queries; stable references required.
   */
  allowNavigation?: (to: string) => boolean;
}

export interface SaveAndGenerateOptions {
  /** Presave confirmations (e.g. regeneration invalidates downstream); false saves/generates nothing. */
  confirm?: () => boolean | Promise<boolean>;
}

export interface EditUnit<T> {
  /** Current contents including unsaved edits. */
  value: T;
  /** Saved contents; compare with `value` for field-subset changes such as sidebar tab marks. */
  savedValue: T;
  setValue: (next: T | ((prev: T) => T)) => void;
  /** Unsaved changes. */
  dirty: boolean;
  status: SaveStatus;
  /** Latest save-failure causes; clear on further editing/discard/successful saves. */
  error: string | null;
  /** Saved contents were externally updated while unsaved edits exist; discarding adopts new contents. */
  externallyUpdated: boolean;
  /** Explanations when external removal/replacement retains visible units. */
  externalChangeMessage?: string;
  /** Submit unsaved edits, returning success; unchanged contents immediately return true. */
  save: () => Promise<boolean>;
  /** Discard unsaved edits, restoring latest saved contents. */
  discard: () => void;
  /**
   * Save and generate: confirm if necessary → save if dirty → generate.
   * Canceled confirmations save nothing; failed saves never generate. Return whether generation executed.
   */
  saveAndGenerate: (generate: () => unknown, options?: SaveAndGenerateOptions) => Promise<boolean>;
}

interface UnitState<T> {
  value: T;
  saved: T;
  /** Most recently observed `source`, identifying external updates. */
  seenSource: T;
  status: SaveStatus;
  error: string | null;
  externallyUpdated: boolean;
}

/**
 * Partially persisted saves (e.g. successful project PATCH followed by failed reference uploads): `saved` becomes new saved contents,
 * restored by discard and used for subsequent saves; `value` contains remaining unsaved contents, defaulting to submitted values.
 * Ordinary errors would retain pre-save saved contents, display old values after discard and write those values back on next saves.
 */
export class PartialSaveError<T> extends Error {
  readonly saved: T;
  readonly value: T | undefined;

  constructor(message: string, { saved, value, cause }: { saved: T; value?: T; cause?: unknown }) {
    super(message, { cause });
    this.name = "PartialSaveError";
    this.saved = saved;
    this.value = value;
  }
}

function jsonEqual<T>(a: T, b: T): boolean {
  return a === b || JSON.stringify(a) === JSON.stringify(b);
}

/**
 * Edit units: UI scopes submitted by single explicit saves (settings views/assets/storyboard details/memory files, etc.).
 * Unit fields first become unsaved edits, submitted together through `save`; automatically register with leave guards while mounted.
 */
export function useEditUnit<T>({
  source,
  save: submit,
  isEqual = jsonEqual,
  leaveTitle,
  allowNavigation,
}: EditUnitOptions<T>): EditUnit<T> {
  const [state, setState] = useState<UnitState<T>>(() => ({
    value: source,
    saved: source,
    seenSource: source,
    status: "idle",
    error: null,
    externallyUpdated: false,
  }));

  // External saved-content changes synchronize during render (React adjusting state to props),
  // avoiding one-frame stale renders. Pushes equal to current saved contents (e.g. own save echoes) are not external updates.
  let current = state;
  if (!isEqual(source, state.seenSource)) {
    if (isEqual(source, state.saved)) {
      current = { ...state, seenSource: source };
    } else if (isEqual(state.value, state.saved)) {
      current = { ...state, seenSource: source, saved: source, value: source, externallyUpdated: false };
    } else {
      current = {
        ...state,
        seenSource: source,
        saved: source,
        externallyUpdated: !isEqual(state.value, source),
      };
    }
    setState(current);
  }

  const { value, saved, status, error } = current;
  const dirty = !isEqual(value, saved);
  const retention = useContext(EditUnitRetentionContext);

  useEffect(() => {
    if (status !== "saved") return;
    const timer = setTimeout(() => {
      setState((prev) => (prev.status === "saved" ? { ...prev, status: "idle" } : prev));
    }, SAVED_DISPLAY_MS);
    return () => clearTimeout(timer);
  }, [status]);

  const setValue = useCallback((next: T | ((prev: T) => T)) => {
    setState((prev) => ({
      ...prev,
      value: typeof next === "function" ? (next as (prev: T) => T)(prev.value) : next,
      status: prev.status === "saving" ? prev.status : "idle",
      error: null,
    }));
  }, []);

  const discard = useCallback(() => {
    setState((prev) => ({ ...prev, value: prev.saved, status: "idle", error: null, externallyUpdated: false }));
  }, []);

  const save = useCallback(async () => {
    if (!dirty) return true;
    const submitted = value;
    setState((prev) => ({ ...prev, status: "saving", error: null }));
    try {
      const result = await submit(submitted, saved);
      const next = result === undefined ? submitted : (result as T);
      setState((prev) => ({
        ...prev,
        saved: next,
        // Adopt server-normalized contents without further edits during saves; otherwise retain new edits.
        value: isEqual(prev.value, submitted) ? next : prev.value,
        status: "saved",
        error: null,
        // Explicit saves remain last-writer-wins, clearing external-update marks.
        externallyUpdated: false,
      }));
      return true;
    } catch (err) {
      if (err instanceof PartialSaveError) {
        const { saved: partial, value: unsaved } = err as PartialSaveError<T>;
        setState((prev) => ({
          ...prev,
          saved: partial,
          value: unsaved !== undefined && isEqual(prev.value, submitted) ? unsaved : prev.value,
          status: "error",
          error: errMsg(err),
          externallyUpdated: false,
        }));
        return false;
      }
      setState((prev) => ({ ...prev, status: "error", error: errMsg(err) }));
      return false;
    }
  }, [dirty, value, saved, submit, isEqual]);

  const saveAndGenerate = useCallback(
    async (generate: () => unknown, options?: SaveAndGenerateOptions) => {
      if (options?.confirm && !(await options.confirm())) return false;
      if (!(await save())) return false;
      await generate();
      return true;
    },
    [save],
  );

  // After external replacement, visible units detach from actual views; old-view exemptions cannot govern active navigation.
  const discarding = useLeaveGuard({ dirty, saving: status === "saving", save, discard, title: leaveTitle, allowNavigation: retention?.message ? undefined : allowNavigation });
  // Pending deletions after discard that remove their own units do not trigger external-removal retention.
  useRetainWhile((dirty || status === "saving") && !discarding);

  return {
    value,
    savedValue: saved,
    setValue,
    dirty,
    status,
    error,
    externallyUpdated: (current.externallyUpdated || Boolean(retention?.message)) && dirty,
    externalChangeMessage: retention?.message,
    save,
    discard,
    saveAndGenerate,
  };
}
