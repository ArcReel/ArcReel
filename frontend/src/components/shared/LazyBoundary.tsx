import { Component, Suspense, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { useLocation } from "wouter";
import { CircleAlert } from "lucide-react";
import { Button } from "@/components/ui/button";
import { LoadingState } from "@/components/shared/LoadingState";

type LazyBoundaryVariant = "screen" | "pane" | "none";

/**
 * Boundaries around lazy regions (`lazyNamed` components): placeholders before chunks arrive, failure/reload
 * notices after final failures, never unmounting application roots.
 *
 * - `screen`: viewport-filling whole-page routes.
 * - `pane`: parent-filling workspace regions, preserving headers/view switching.
 * - `none`: no loading/failure DOM for components such as onboarding.
 *
 * React.lazy caches failures, so in-place retry cannot obtain new chunks; offer whole-page reload only.
 * Route changes clear failures, preserving other pages after users leave failed routes.
 */
export function LazyBoundary({ variant, children }: { variant: LazyBoundaryVariant; children: ReactNode }) {
  const [location] = useLocation();
  return (
    <LoadErrorBoundary variant={variant} resetKey={location}>
      <Suspense fallback={variant === "none" ? null : <LoadingState variant={variant} />}>{children}</Suspense>
    </LoadErrorBoundary>
  );
}

interface LoadErrorBoundaryProps {
  variant: LazyBoundaryVariant;
  resetKey: string;
  children: ReactNode;
}

interface LoadErrorBoundaryState {
  failed: boolean;
  resetKey: string;
}

class LoadErrorBoundary extends Component<LoadErrorBoundaryProps, LoadErrorBoundaryState> {
  state: LoadErrorBoundaryState = { failed: false, resetKey: this.props.resetKey };

  static getDerivedStateFromError(): Partial<LoadErrorBoundaryState> {
    return { failed: true };
  }

  static getDerivedStateFromProps(
    props: LoadErrorBoundaryProps,
    state: LoadErrorBoundaryState,
  ): Partial<LoadErrorBoundaryState> | null {
    return props.resetKey === state.resetKey ? null : { failed: false, resetKey: props.resetKey };
  }

  render() {
    if (!this.state.failed) return this.props.children;
    if (this.props.variant === "none") return null;
    return <LoadFailedState variant={this.props.variant} />;
  }
}

function LoadFailedState({ variant }: { variant: "screen" | "pane" }) {
  const { t } = useTranslation("common");
  return (
    <div
      role="alert"
      className={
        variant === "screen"
          ? "flex h-dvh flex-col items-center justify-center gap-3 bg-background text-sm text-muted-foreground"
          : "flex h-full flex-col items-center justify-center gap-3 text-sm text-muted-foreground"
      }
    >
      <p className="flex items-center gap-2">
        <CircleAlert aria-hidden className="size-4" />
        {t("page_load_failed")}
      </p>
      <Button variant="outline" size="sm" onClick={() => window.location.reload()}>
        {t("reload_page")}
      </Button>
    </div>
  );
}
