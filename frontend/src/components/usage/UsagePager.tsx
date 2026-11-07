import { ChevronLeft, ChevronRight } from "lucide-react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";

interface UsagePagerProps {
  from: number;
  to: number;
  total: number;
  hasPrev: boolean;
  hasNext: boolean;
  onPage: (direction: "prev" | "next") => void;
}

/** Pagination footer shared by records and session tables: current position and previous/next buttons. */
export function UsagePager({ from, to, total, hasPrev, hasNext, onPage }: UsagePagerProps) {
  const { t } = useTranslation("dashboard");
  return (
    <footer className="flex items-center justify-between border-t border-border px-4 py-2">
      <span className="num text-xs text-muted-foreground">{t("usage_page_position", { from, to, total })}</span>
      <div className="flex items-center gap-1">
        <Button
          size="icon-sm"
          variant="ghost"
          aria-label={t("usage_prev_page")}
          disabled={!hasPrev}
          onClick={() => onPage("prev")}
        >
          <ChevronLeft aria-hidden="true" />
        </Button>
        <Button
          size="icon-sm"
          variant="ghost"
          aria-label={t("usage_next_page")}
          disabled={!hasNext}
          onClick={() => onPage("next")}
        >
          <ChevronRight aria-hidden="true" />
        </Button>
      </div>
    </footer>
  );
}
