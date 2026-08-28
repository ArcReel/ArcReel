import { Component, type ErrorInfo, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

interface Props {
  children: ReactNode;
  fallback?: ReactNode;
}

interface State {
  hasError: boolean;
  error: Error | null;
}

/**
 * 全局错误边界：捕获渲染期间的同步错误，避免整棵 React 树被卸载导致页面空白。
 * 各页面此前在交互回调中因 `voidPromise` 对同步返回值的 `.catch` 误用而抛出的
 * `TypeError: Cannot read properties of undefined (reading 'catch')` 会经此边界捕获，
 * 展示可恢复的错误态而非空白页面。修复后 `voidPromise` 已健壮，但边界仍保留作为
 * 最后防线，防止未来新增的同步回调再次导致空白。
 */
export class ErrorBoundary extends Component<Props, State> {
  state: State = { hasError: false, error: null };

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error("ErrorBoundary caught", error, info);
  }

  handleReset = (): void => {
    this.setState({ hasError: false, error: null });
  };

  render(): ReactNode {
    if (this.state.hasError) {
      if (this.props.fallback) return this.props.fallback;
      return <ErrorFallback error={this.state.error} onReset={this.handleReset} />;
    }
    return this.props.children;
  }
}

function ErrorFallback({ error, onReset }: { error: Error | null; onReset: () => void }) {
  const { t } = useTranslation("common");
  return (
    <div
      role="alert"
      className="flex min-h-[240px] flex-col items-center justify-center gap-3 p-8 text-center"
    >
      <p className="text-[13px] font-medium text-text-2">{t("common:unexpected_error") ?? "Something went wrong"}</p>
      {error && <p className="max-w-md font-mono text-[11px] text-text-4">{error.message}</p>}
      <button
        type="button"
        onClick={() => {
          onReset();
          window.location.reload();
        }}
        className="rounded-md bg-[var(--color-accent)] px-3 py-1.5 text-[12px] font-medium text-white"
      >
        {t("common:retry") ?? "Retry"}
      </button>
    </div>
  );
}
