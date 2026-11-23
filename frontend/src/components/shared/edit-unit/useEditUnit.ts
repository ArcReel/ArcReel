import { useCallback, useContext, useEffect, useState } from "react";

import { errMsg } from "@/utils/async";

import { EditUnitRetentionContext, useRetainWhile } from "./edit-unit-retention";

import { useLeaveGuard } from "./LeaveGuard";

export type SaveStatus = "idle" | "saving" | "saved" | "error";

const SAVED_DISPLAY_MS = 2000;

export interface EditUnitOptions<T> {
  source: T;
  save: (value: T, savedValue: T) => Promise<T | void>;
  isEqual?: (a: T, b: T) => boolean;
  leaveTitle?: string;
  allowNavigation?: (to: string) => boolean;
}

export interface SaveAndGenerateOptions {
  confirm?: () => boolean | Promise<boolean>;
}

export interface EditUnit<T> {
  value: T;
  savedValue: T;
  setValue: (next: T | ((prev: T) => T)) => void;
  dirty: boolean;
  status: SaveStatus;
  error: string | null;
  externallyUpdated: boolean;
  externalChangeMessage?: string;
  save: () => Promise<boolean>;
  discard: () => void;
  saveAndGenerate: (generate: () => unknown, options?: SaveAndGenerateOptions) => Promise<boolean>;
}

interface UnitState<T> {
  value: T;
  saved: T;
  seenSource: T;
  status: SaveStatus;
  error: string | null;
  externallyUpdated: boolean;
}

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
        value: isEqual(prev.value, submitted) ? next : prev.value,
        status: "saved",
        error: null,
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

  const discarding = useLeaveGuard({ dirty, saving: status === "saving", save, discard, title: leaveTitle, allowNavigation: retention?.message ? undefined : allowNavigation });
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
