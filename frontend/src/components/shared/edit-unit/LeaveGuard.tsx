import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useId,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { Loader2 } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Router, useLocation, useSearch, type AroundNavHandler } from "wouter";

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogBody,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";

export interface LeaveGuardOptions {
  /** Unsaved changes; false disables interception. */
  dirty: boolean;
  /**
   * Saves are pending. Departures first await saves, then proceed if successful without new edits; failures/new edits ask.
   * Otherwise Discard edits would release already-submitted saves, while Save and leave would submit duplicates.
   */
  saving?: boolean;
  /** Save and leave callbacks return success; failures stay in place, with edit units displaying errors. */
  save: () => Promise<boolean>;
  /** Discard edits callbacks; omission only permits navigation, for pages where all intercepted exits unmount/reload edit units. */
  discard?: () => void;
  /** Dialog titles, e.g. Storyboard 3 has unsaved changes; used when only one edit unit is dirty. */
  title?: string;
  /** Do not intercept in-app navigation returning true; arguments are target paths including queries. */
  allowNavigation?: (to: string) => boolean;
}

/**
 * Actions wrapped by leave guards. Synchronous actions return `void`; permission immediately discards edits. Settling actions (delete/remove requests)
 * return `Promise<boolean>`: discard each edit unit only after success; failures, server re-confirmation requirements or occupation blocks
 * return `false` and preserve edits unchanged. Actions own failure notifications; rejections count as failures and still propagate.
 */
export type LeaveAction = () => void | Promise<boolean>;

export interface ConfirmLeaveOptions {
  /** Third-button copy, default Save and leave; e.g. Save and switch for storyboard switching. */
  saveLabel?: string;
}

interface LeaveRequest extends ConfirmLeaveOptions {
  /** Edit units affected by this departure. */
  unitIds: string[];
  title?: string;
  proceed: LeaveAction;
}

interface RequestLeaveOptions extends ConfirmLeaveOptions {
  to?: string;
  /** Intercepted browser Forward/Back: ask even for discarded edits while async actions remain pending. */
  fromHistory?: boolean;
}

interface PendingLeave {
  proceed: LeaveAction;
  options: RequestLeaveOptions;
}

interface RegisteredUnit extends LeaveGuardOptions {
  /** Users chose Discard edits for still-pending async actions; true until settlement. */
  setDiscarding: (discarding: boolean) => void;
}

interface LeaveGuardRegistry {
  register: (id: string, unit: RegisteredUnit) => () => void;
  confirmLeave: (proceed: LeaveAction, options?: ConfirmLeaveOptions) => void;
  hasUnsavedChanges: () => boolean;
}

function isPromise(value: unknown): value is Promise<boolean> {
  return typeof (value as Promise<boolean> | undefined)?.then === "function";
}

const LeaveGuardContext = createContext<LeaveGuardRegistry | null>(null);

/**
 * Register edit units with leave guards: unsaved changes while mounted prompt before in-app navigation, `useConfirmLeave`-
 * wrapped switches and tab closing. `useEditUnit` registers automatically; pages managing form state directly call this.
 * Function arguments require stable references.
 *
 * True means users chose to discard edits and await wrapped async actions: edits remain until successful settlement.
 * Units retained against external removal stop requiring retention at this point; removals caused by actions apply directly.
 */
export function useLeaveGuard({ dirty, saving, save, discard, title, allowNavigation }: LeaveGuardOptions): boolean {
  const registry = useContext(LeaveGuardContext);
  const id = useId();
  const [discarding, setDiscarding] = useState(false);
  useEffect(() => {
    if (!registry) return;
    return registry.register(id, { dirty, saving, save, discard, title, allowNavigation, setDiscarding });
  }, [registry, id, dirty, saving, save, discard, title, allowNavigation]);
  return discarding;
}

/**
 * For switching selections within master/detail layouts (providers, memory files, previous/next storyboards/assets):
 * unsaved changes prompt first, then permitted users execute `proceed`. Switching `proceed` must switch synchronously;
 * route changes initiated there bypass repeat interception. URL-held selections switched by routing are already covered and need no wrapping.
 *
 * Potentially failing delete/remove actions return `Promise<boolean>` (see `LeaveAction`): after Discard edits,
 * retain edits until settlement, discarding only on success; while pending, these relinquished edits no longer intercept action-initiated routing or
 * `useConfirmLeave`, while new edits in other units prompt normally.
 */
export function useConfirmLeave(): (proceed: LeaveAction, options?: ConfirmLeaveOptions) => void {
  const registry = useContext(LeaveGuardContext);
  return useCallback(
    (proceed: LeaveAction, options?: ConfirmLeaveOptions) => {
      if (registry) registry.confirmLeave(proceed, options);
      else void proceed();
    },
    [registry],
  );
}

/**
 * Return stable query functions checking whether mounted edit units currently contain unsaved changes.
 * For automatic behavior that must not interrupt editing (e.g. Agent-change auto-location); user-initiated switches still use `useConfirmLeave`.
 */
export function useHasUnsavedChanges(): () => boolean {
  const registry = useContext(LeaveGuardContext);
  return useCallback(() => registry?.hasUnsavedChanges() ?? false, [registry]);
}

function currentUrl(): string {
  return window.location.pathname + window.location.search + window.location.hash;
}

/** Target/current paths and query parameters match (ignoring parameter order); `to` may be query-only relative URLs. */
function isSameLocation(to: string, here: string): boolean {
  const current = new URL(here, "http://leave-guard.invalid");
  const target = new URL(to, current);
  current.searchParams.sort();
  target.searchParams.sort();
  return target.pathname === current.pathname && target.search === current.search;
}

/**
 * Leave-guard registries and sole interception dialogs, mounted at route roots (empty `base`).
 * Intercept in-app navigation through wouter `aroundNav`; browser Forward/Back first returns to original URLs, then asks;
 * tab closing/refreshing uses native browser `beforeunload` prompts.
 */
export function LeaveGuardProvider({ children }: { children: ReactNode }) {
  const { t } = useTranslation("common");
  const [path] = useLocation();
  const search = useSearch();
  // Current route paths/queries let aroundNav recognize navigation to current addresses.
  const hereRef = useRef("");
  useLayoutEffect(() => {
    hereRef.current = search ? `${path}?${search}` : path;
  }, [path, search]);
  const unitsRef = useRef(new Map<string, RegisteredUnit>());
  // Do not intercept navigation again during synchronous execution of user-permitted switches.
  const passingRef = useRef(false);
  // Edit units/action counts involved in permitted pending async actions: until settlement, edits are authorized for discarding,
  // bypassing action-initiated navigation interception; other units ask normally, and browser Forward/Back still asks.
  const settlingRef = useRef(new Map<string, number>());
  // After users permit intercepted Forward/Back, pass the history.back()-caused popstate directly to wouter.
  const releasingPopRef = useRef(false);
  // Departure requests awaiting saves; reevaluate on registration changes and retain only the latest.
  const pendingLeaveRef = useRef<PendingLeave | null>(null);
  const lastUrlRef = useRef("");
  const [request, setRequest] = useState<LeaveRequest | null>(null);
  const [open, setOpen] = useState(false);
  const [saving, setSaving] = useState(false);

  /** Edit units affected by departures, excluding those allowing these targets. */
  const leavingUnits = useCallback(
    (to?: string) => [...unitsRef.current].filter(([, unit]) => !(to !== undefined && unit.allowNavigation?.(to))),
    [],
  );

  const dirtyUnitIds = useCallback(
    (to?: string) => leavingUnits(to).flatMap(([id, unit]) => (unit.dirty ? [id] : [])),
    [leavingUnits],
  );

  /**
   * Execute permitted actions and discard units in `discardIds`. Discard immediately after synchronous completion;
   * async actions discard only after successful settlement, notifying units to stop requiring external-removal retention while pending and restoring on failure.
   */
  const pass = useCallback((proceed: LeaveAction, discardIds: readonly string[] = []) => {
    const units = () => discardIds.flatMap((id) => unitsRef.current.get(id) ?? []);
    const affected = units();
    let result: void | Promise<boolean>;
    passingRef.current = true;
    try {
      result = proceed();
    } catch (err) {
      for (const unit of affected) unit.discard?.();
      throw err;
    } finally {
      passingRef.current = false;
    }
    if (!isPromise(result)) {
      for (const unit of affected) unit.discard?.();
      return;
    }
    const settling = settlingRef.current;
    for (const id of discardIds) settling.set(id, (settling.get(id) ?? 0) + 1);
    for (const unit of affected) unit.setDiscarding(true);
    const settle = (succeeded: boolean) => {
      for (const id of discardIds) {
        const count = (settling.get(id) ?? 1) - 1;
        if (count > 0) settling.set(id, count);
        else settling.delete(id);
      }
      // Read latest registrations; units unmounted after successful actions need no discard.
      for (const unit of units()) {
        unit.setDiscarding(false);
        if (succeeded) unit.discard?.();
      }
    };
    void result.then(
      (succeeded) => settle(succeeded),
      (err: unknown) => {
        settle(false);
        throw err;
      },
    );
  }, []);

  const requestLeave = useCallback(
    (proceed: LeaveAction, options: RequestLeaveOptions = {}) => {
      if (passingRef.current) {
        void proceed();
        return;
      }
      const units = leavingUnits(options.to).filter(([id]) => options.fromHistory || !settlingRef.current.has(id));
      if (units.some(([, unit]) => unit.saving)) {
        pendingLeaveRef.current = { proceed, options };
        return;
      }
      const unitIds = units.flatMap(([id, unit]) => (unit.dirty ? [id] : []));
      if (unitIds.length === 0) {
        void proceed();
        return;
      }
      const title = unitIds.length === 1 ? unitsRef.current.get(unitIds[0])?.title : undefined;
      setRequest({ unitIds, title, proceed, saveLabel: options.saveLabel });
      setOpen(true);
    },
    [leavingUnits],
  );

  const registry = useMemo<LeaveGuardRegistry>(() => {
    // Save settlement updates edit-unit registrations; reevaluate deferred departures then, outside effects.
    const retryPendingLeave = () => {
      const pending = pendingLeaveRef.current;
      if (!pending) return;
      pendingLeaveRef.current = null;
      queueMicrotask(() => requestLeave(pending.proceed, pending.options));
    };
    return {
      register: (id, unit) => {
        unitsRef.current.set(id, unit);
        retryPendingLeave();
        return () => {
          unitsRef.current.delete(id);
          retryPendingLeave();
        };
      },
      confirmLeave: (proceed, options) => requestLeave(proceed, options),
      hasUnsavedChanges: () => dirtyUnitIds().length > 0,
    };
  }, [requestLeave, dirtyUnitIds]);

  const aroundNav = useCallback<AroundNavHandler>(
    (go, to, options) => {
      const navigate = () => {
        go(to, options);
        lastUrlRef.current = currentUrl();
      };
      // Navigation to current addresses (e.g. clicking selected sections again) unmounts no edit units and needs no prompt.
      if (isSameLocation(to, hereRef.current)) navigate();
      else requestLeave(navigate, { to });
    },
    [requestLeave],
  );

  // Register before wouter location subscriptions (child passive effects), listening in capture
  // so wouter never receives popstate while unsaved changes exist.
  useLayoutEffect(() => {
    lastUrlRef.current = currentUrl();
    const onPopState = (event: PopStateEvent) => {
      const target = currentUrl();
      // Pending-save units cannot permit exits even without current edits; failed saves/new edits during saving still require prompts.
      const unaffected =
        dirtyUnitIds(target).length === 0 && !leavingUnits(target).some(([, unit]) => unit.saving);
      if (releasingPopRef.current || passingRef.current || unaffected) {
        releasingPopRef.current = false;
        lastUrlRef.current = target;
        return;
      }
      // Browser Forward/Back bypasses aroundNav and already changed URLs: push pre-departure addresses so target records sit immediately before them.
      // After permission, go back one record to original browser targets without pushing new records.
      event.stopImmediatePropagation();
      window.history.pushState(null, "", lastUrlRef.current);
      requestLeave(
        () => {
          releasingPopRef.current = true;
          window.history.back();
        },
        { to: target, fromHistory: true },
      );
    };
    window.addEventListener("popstate", onPopState, { capture: true });
    return () => window.removeEventListener("popstate", onPopState, { capture: true });
  }, [dirtyUnitIds, leavingUnits, requestLeave]);

  useEffect(() => {
    const onBeforeUnload = (event: BeforeUnloadEvent) => {
      if (dirtyUnitIds().length > 0) event.preventDefault();
    };
    window.addEventListener("beforeunload", onBeforeUnload);
    return () => window.removeEventListener("beforeunload", onBeforeUnload);
  }, [dirtyUnitIds]);

  const discardAll = () => {
    if (!request) return;
    setOpen(false);
    pass(request.proceed, request.unitIds);
  };

  const saveAll = async () => {
    if (!request) return;
    setSaving(true);
    let saved = true;
    try {
      for (const id of request.unitIds) {
        // Read latest registrations each time; preceding units rerender/update registrations after saves.
        const unit = unitsRef.current.get(id);
        if (unit && !(await unit.save())) {
          saved = false;
          break;
        }
      }
    } finally {
      setSaving(false);
      setOpen(false);
    }
    // Failed saves stay in place; errors appear in edit-unit save bars/notices.
    if (saved) pass(request.proceed);
  };

  return (
    <LeaveGuardContext.Provider value={registry}>
      <Router aroundNav={aroundNav}>{children}</Router>
      <AlertDialog
        open={open}
        onOpenChange={(next) => {
          // Ignore Esc during saves so dialogs never close while requests remain pending.
          if (!next && !saving) setOpen(false);
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{request?.title ?? t("unsaved_changes")}</AlertDialogTitle>
          </AlertDialogHeader>
          <AlertDialogBody tabIndex={0} role="region" aria-label={request?.title ?? t("unsaved_changes")}>
            <AlertDialogDescription>{t("leave_dialog_description")}</AlertDialogDescription>
          </AlertDialogBody>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={saving}>{t("keep_editing")}</AlertDialogCancel>
            <AlertDialogAction variant="destructive" disabled={saving} onClick={discardAll}>
              {t("discard_changes")}
            </AlertDialogAction>
            <AlertDialogAction disabled={saving} onClick={() => void saveAll()}>
              {saving ? <Loader2 aria-hidden data-icon="inline-start" className="animate-spin" /> : null}
              {request?.saveLabel ?? t("save_and_leave")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </LeaveGuardContext.Provider>
  );
}
