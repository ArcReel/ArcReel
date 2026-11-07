import type { ReactNode } from "react";
import { Link } from "wouter";
import { ArrowLeft, MoreHorizontal } from "lucide-react";
import { useTranslation } from "react-i18next";
import { providerSettingsPath } from "@/app-routes";
import { TruncatedText } from "@/components/shared/TruncatedText";
import { Button, buttonVariants } from "@/components/ui/button";
import { DropdownMenu, DropdownMenuContent, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";

/** Custom-provider return targets, parsed from address `from`. */
export interface EndpointBackTarget {
  providerId: number;
  providerName: string;
}

interface EndpointHeaderProps {
  back: EndpointBackTarget | null;
  /** Titles; editable ComfyUI endpoint names show current values. */
  title: string;
  /** Type/status badges after titles. */
  badges?: ReactNode;
  /** Subtitle rows: authors/versions, origins, node counts, etc. */
  meta?: ReactNode;
  /** Main header actions: New provider using endpoint, Copy to my endpoints or Reimport. */
  primary?: ReactNode;
  /** More-menu entries; no entries means hidden buttons. */
  menu?: ReactNode;
}

/** Endpoint-detail headers: return providers, names/badges, primary/More actions. */
export function EndpointHeader({ back, title, badges, meta, primary, menu }: EndpointHeaderProps) {
  const { t } = useTranslation("dashboard");
  return (
    <div className="flex flex-col gap-2">
      {back && (
        <div>
          <Link
            href={providerSettingsPath({ custom: back.providerId })}
            className={buttonVariants({ variant: "ghost", size: "sm" })}
          >
            <ArrowLeft aria-hidden data-icon="inline-start" />
            {t("ce_back_to_provider", { name: back.providerName })}
          </Link>
        </div>
      )}
      <div className="flex items-start gap-3">
        <div className="flex min-w-0 flex-1 flex-col gap-1">
          <div className="flex min-w-0 flex-wrap items-center gap-2">
            <h2 className="min-w-0 text-lg font-medium">
              <TruncatedText text={title} />
            </h2>
            {badges}
          </div>
          {meta && (
            <div className="flex flex-wrap items-center gap-x-3 gap-y-0.5 text-sm text-muted-foreground">{meta}</div>
          )}
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {primary}
          {menu && (
            <DropdownMenu>
              <DropdownMenuTrigger render={<Button variant="ghost" size="icon" aria-label={t("ce_more_actions")} />}>
                <MoreHorizontal aria-hidden />
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end" className="w-56">
                {menu}
              </DropdownMenuContent>
            </DropdownMenu>
          )}
        </div>
      </div>
    </div>
  );
}
