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
  /** 有未保存修改；为 false 时不拦截。 */
  dirty: boolean;
  /**
   * 保存请求在途。此时的离开先等保存落定再判断：成功且没有新修改就直接放行，失败或又有修改才询问。
   * 否则「放弃修改」会放走已经发出的保存，「保存并离开」会重复提交。
   */
  saving?: boolean;
  /** 「保存并离开」时调用，返回是否保存成功。失败时留在原处，错误由编辑单元自己显示。 */
  save: () => Promise<boolean>;
  /** 「放弃修改」时调用。不传时只放行跳转，适用于被拦截的出口都会卸载或重新加载编辑单元的页面。 */
  discard?: () => void;
  /** 对话框标题，如「分镜 3 有未保存的修改」；只有一个编辑单元有修改时采用。 */
  title?: string;
  /** 对返回 true 的应用内导航不拦截。参数是带查询串的目标路径。 */
  allowNavigation?: (to: string) => boolean;
}

export interface ConfirmLeaveOptions {
  /** 第三个按钮的文案，缺省为「保存并离开」；如切换分镜时传「保存并切换」。 */
  saveLabel?: string;
}

interface LeaveRequest extends ConfirmLeaveOptions {
  /** 被这次离开影响的编辑单元。 */
  unitIds: string[];
  title?: string;
  proceed: () => void;
}

interface PendingLeave {
  proceed: () => void;
  options: ConfirmLeaveOptions & { to?: string };
}

interface LeaveGuardRegistry {
  register: (id: string, unit: LeaveGuardOptions) => () => void;
  confirmLeave: (proceed: () => void, options?: ConfirmLeaveOptions) => void;
}

const LeaveGuardContext = createContext<LeaveGuardRegistry | null>(null);

/**
 * 把一个编辑单元登记到离开拦截：挂载期间有未保存修改时，应用内路由跳转、`useConfirmLeave`
 * 包住的切换与关闭标签页都会先询问。`useEditUnit` 已自动登记；自行管理表单状态的页面直接调用。
 * 函数型参数需传稳定引用。
 */
export function useLeaveGuard({ dirty, saving, save, discard, title, allowNavigation }: LeaveGuardOptions): void {
  const registry = useContext(LeaveGuardContext);
  const id = useId();
  useEffect(() => {
    if (!registry) return;
    return registry.register(id, { dirty, saving, save, discard, title, allowNavigation });
  }, [registry, id, dirty, saving, save, discard, title, allowNavigation]);
}

/**
 * 主从布局内切换选中项（供应商、记忆文件、分镜、资产的上一个与下一个）时使用：
 * 有未保存修改先弹出拦截对话框，用户放行后才执行 `proceed`。`proceed` 需同步完成切换，
 * 其中发起的路由跳转不再重复拦截。选中项记在 URL 里、经路由跳转切换的，路由拦截已经覆盖，不必再包。
 */
export function useConfirmLeave(): (proceed: () => void, options?: ConfirmLeaveOptions) => void {
  const registry = useContext(LeaveGuardContext);
  return useCallback(
    (proceed: () => void, options?: ConfirmLeaveOptions) => {
      if (registry) registry.confirmLeave(proceed, options);
      else proceed();
    },
    [registry],
  );
}

function currentUrl(): string {
  return window.location.pathname + window.location.search + window.location.hash;
}

/** 目标与当前地址的路径和查询参数都相同（参数顺序不计）。`to` 可以是只有查询串的相对地址。 */
function isSameLocation(to: string, here: string): boolean {
  const current = new URL(here, "http://leave-guard.invalid");
  const target = new URL(to, current);
  current.searchParams.sort();
  target.searchParams.sort();
  return target.pathname === current.pathname && target.search === current.search;
}

/**
 * 离开拦截的注册中心与唯一的拦截对话框，挂在路由根部（`base` 为空的位置）。
 * 应用内路由跳转经 wouter 的 `aroundNav` 拦截；浏览器前进后退先退回原地址再询问；
 * 关闭标签页与刷新用浏览器原生的 `beforeunload` 提示。
 */
export function LeaveGuardProvider({ children }: { children: ReactNode }) {
  const { t } = useTranslation("common");
  const [path] = useLocation();
  const search = useSearch();
  // 路由的当前地址（路径加查询串），供 aroundNav 识别跳到当前地址
  const hereRef = useRef("");
  useLayoutEffect(() => {
    hereRef.current = search ? `${path}?${search}` : path;
  }, [path, search]);
  const unitsRef = useRef(new Map<string, LeaveGuardOptions>());
  // 用户已放行的那次切换在同步执行期间发起的跳转，不再重复拦截
  const passingRef = useRef(false);
  // 用户放行被截住的前进后退后，由 history.back() 引起的那次 popstate 直接交给 wouter
  const releasingPopRef = useRef(false);
  // 等在途保存落定的离开请求；登记变化时重新判断，只保留最近一次
  const pendingLeaveRef = useRef<PendingLeave | null>(null);
  const lastUrlRef = useRef("");
  const [request, setRequest] = useState<LeaveRequest | null>(null);
  const [open, setOpen] = useState(false);
  const [saving, setSaving] = useState(false);

  /** 这次离开会影响的编辑单元：排除放行该目标的单元。 */
  const leavingUnits = useCallback(
    (to?: string) => [...unitsRef.current].filter(([, unit]) => !(to !== undefined && unit.allowNavigation?.(to))),
    [],
  );

  const dirtyUnitIds = useCallback(
    (to?: string) => leavingUnits(to).flatMap(([id, unit]) => (unit.dirty ? [id] : [])),
    [leavingUnits],
  );

  const pass = useCallback((proceed: () => void) => {
    passingRef.current = true;
    try {
      proceed();
    } finally {
      passingRef.current = false;
    }
  }, []);

  const requestLeave = useCallback(
    (proceed: () => void, options: ConfirmLeaveOptions & { to?: string } = {}) => {
      if (passingRef.current) {
        proceed();
        return;
      }
      if (leavingUnits(options.to).some(([, unit]) => unit.saving)) {
        pendingLeaveRef.current = { proceed, options };
        return;
      }
      const unitIds = dirtyUnitIds(options.to);
      if (unitIds.length === 0) {
        proceed();
        return;
      }
      const title = unitIds.length === 1 ? unitsRef.current.get(unitIds[0])?.title : undefined;
      setRequest({ unitIds, title, proceed, saveLabel: options.saveLabel });
      setOpen(true);
    },
    [leavingUnits, dirtyUnitIds],
  );

  const registry = useMemo<LeaveGuardRegistry>(() => {
    // 编辑单元的保存落定后会更新登记，此时重新判断被推迟的离开；放到 effect 之外执行
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
    };
  }, [requestLeave]);

  const aroundNav = useCallback<AroundNavHandler>(
    (go, to, options) => {
      const navigate = () => {
        go(to, options);
        lastUrlRef.current = currentUrl();
      };
      // 跳到当前地址（如再点一次已选中的分区）不会卸载任何编辑单元，不询问
      if (isSameLocation(to, hereRef.current)) navigate();
      else requestLeave(navigate, { to });
    },
    [requestLeave],
  );

  // 先于 wouter 的位置订阅（子组件的 passive effect）登记，并在捕获阶段监听，
  // 保证有未保存修改时 wouter 收不到这次 popstate。
  useLayoutEffect(() => {
    lastUrlRef.current = currentUrl();
    const onPopState = (event: PopStateEvent) => {
      const target = currentUrl();
      if (releasingPopRef.current || passingRef.current || dirtyUnitIds(target).length === 0) {
        releasingPopRef.current = false;
        lastUrlRef.current = target;
        return;
      }
      // 浏览器前进后退不经过 aroundNav，地址已经变了：先压入离开前的地址，目标记录正好在它前一条。
      // 用户放行后退回一条，回到浏览器原本要去的那条历史记录，不另压新记录
      event.stopImmediatePropagation();
      window.history.pushState(null, "", lastUrlRef.current);
      requestLeave(
        () => {
          releasingPopRef.current = true;
          window.history.back();
        },
        { to: target },
      );
    };
    window.addEventListener("popstate", onPopState, { capture: true });
    return () => window.removeEventListener("popstate", onPopState, { capture: true });
  }, [dirtyUnitIds, requestLeave]);

  useEffect(() => {
    const onBeforeUnload = (event: BeforeUnloadEvent) => {
      if (dirtyUnitIds().length > 0) event.preventDefault();
    };
    window.addEventListener("beforeunload", onBeforeUnload);
    return () => window.removeEventListener("beforeunload", onBeforeUnload);
  }, [dirtyUnitIds]);

  const discardAll = () => {
    if (!request) return;
    for (const id of request.unitIds) unitsRef.current.get(id)?.discard?.();
    setOpen(false);
    pass(request.proceed);
  };

  const saveAll = async () => {
    if (!request) return;
    setSaving(true);
    let saved = true;
    for (const id of request.unitIds) {
      // 每次取最新登记：前一个单元保存后会重新渲染并更新登记
      const unit = unitsRef.current.get(id);
      if (unit && !(await unit.save())) {
        saved = false;
        break;
      }
    }
    setSaving(false);
    setOpen(false);
    // 保存失败留在原处，错误显示在编辑单元的保存栏或提示条上
    if (saved) pass(request.proceed);
  };

  return (
    <LeaveGuardContext.Provider value={registry}>
      <Router aroundNav={aroundNav}>{children}</Router>
      <AlertDialog
        open={open}
        onOpenChange={(next) => {
          // 保存中不响应 Esc，避免请求在途时对话框先关掉
          if (!next && !saving) setOpen(false);
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{request?.title ?? t("unsaved_changes")}</AlertDialogTitle>
          </AlertDialogHeader>
          <AlertDialogBody>
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
