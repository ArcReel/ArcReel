import { Component, type ErrorInfo, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { CircleAlert } from "lucide-react";
import { Button } from "@/components/ui/button";

/**
 * 应用根节点边界：`main.tsx` 在 `AppRoutes` 外只包了这一层。
 *
 * 路由级的 `LazyBoundary` 只接住懒加载区域的失败；任何其它未捕获的渲染错误在 React 19
 * 下会让整棵树卸载，`#app-root` 变成空白——用户看到的就是白屏，且没有任何可操作的入口。
 * 这里兜住这类错误，显示失败说明与「重新加载」，至少留一条恢复路径。
 *
 * 边界自身不使用路由与业务状态：出错时这些可能已经不可用。
 */
export class RootErrorBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    // 没有错误上报通道，控制台是唯一可观测出口：保留错误与组件栈，便于用户反馈时取用。
    console.error("unhandled render error", error, info.componentStack);
  }

  render() {
    if (!this.state.failed) return this.props.children;
    return <RootFailedState />;
  }
}

function RootFailedState() {
  const { t } = useTranslation("common");
  return (
    <div
      role="alert"
      className="flex h-dvh flex-col items-center justify-center gap-3 bg-background px-6 text-center text-sm text-muted-foreground"
    >
      <p className="flex items-center gap-2">
        <CircleAlert aria-hidden className="size-4" />
        {t("app_crashed")}
      </p>
      <p className="max-w-72 text-xs">{t("app_crashed_hint")}</p>
      <Button variant="outline" size="sm" onClick={() => window.location.reload()}>
        {t("reload_page")}
      </Button>
    </div>
  );
}
