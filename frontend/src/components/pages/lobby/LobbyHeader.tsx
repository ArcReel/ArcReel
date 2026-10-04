import { useEffect, useRef, type ReactNode } from "react";
import { Link } from "wouter";
import { Bot, ChevronDown, FileArchive, Library, Loader2, Plus, Search, Settings } from "lucide-react";
import { useTranslation } from "react-i18next";
import { cn } from "cn";
import { ROUTE_APP_ASSETS, ROUTE_APP_SETTINGS, settingsSectionPath } from "@/app-routes";
import { BRAND } from "@/branding";
import { PageHeader } from "@/components/shared/page-shell/PageHeader";
import { Button, buttonVariants } from "@/components/ui/button";
import { ButtonGroup } from "@/components/ui/button-group";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { InputGroup, InputGroupAddon, InputGroupInput, InputGroupText } from "@/components/ui/input-group";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { ONBOARDING_ANCHORS } from "@/onboarding/anchors";
import { useConfigStatusStore } from "@/stores/config-status-store";

interface LobbyHeaderProps {
  query: string;
  onQueryChange: (query: string) => void;
  onCreate: () => void;
  onImport: () => void;
  importing: boolean;
}

/** 大厅顶栏：品牌与搜索靠左；资产库、外部 Agent 接入、「新建项目」分体按钮与设置靠右。 */
export function LobbyHeader({ query, onQueryChange, onCreate, onImport, importing }: LobbyHeaderProps) {
  const { t } = useTranslation(["dashboard", "common", "assets"]);
  const configComplete = useConfigStatusStore((s) => s.isComplete);
  const searchRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const focusSearch = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        searchRef.current?.focus();
      }
    };
    window.addEventListener("keydown", focusSearch);
    return () => window.removeEventListener("keydown", focusSearch);
  }, []);

  return (
    <PageHeader
      actions={
        <>
          <Link href={ROUTE_APP_ASSETS} className={buttonVariants({ variant: "ghost" })}>
            <Library aria-hidden data-icon="inline-start" />
            {t("assets:library_title")}
          </Link>
          <IconLink href={settingsSectionPath("external-agent")} label={t("dashboard:settings_external_agent")}>
            <Bot aria-hidden />
          </IconLink>
          <span aria-hidden className="mx-1 h-5 w-px bg-border" />
          <ButtonGroup>
            <Button onClick={onCreate} data-onboarding={ONBOARDING_ANCHORS.lobbyCreateProject}>
              <Plus aria-hidden data-icon="inline-start" />
              {t("dashboard:lobby_new_project")}
            </Button>
            <DropdownMenu>
              <DropdownMenuTrigger
                render={
                  <Button size="icon" aria-label={t("dashboard:lobby_more_create_options")} aria-busy={importing} />
                }
              >
                {importing ? <Loader2 aria-hidden className="animate-spin" /> : <ChevronDown aria-hidden />}
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end">
                <DropdownMenuItem disabled={importing} onClick={onImport}>
                  <FileArchive aria-hidden />
                  {t("dashboard:lobby_import_zip_ellipsis")}
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          </ButtonGroup>
          <IconLink
            href={ROUTE_APP_SETTINGS}
            label={t("common:settings")}
            onboardingAnchor={ONBOARDING_ANCHORS.lobbySettings}
          >
            <Settings aria-hidden />
            {!configComplete && (
              <span
                role="img"
                aria-label={t("common:config_incomplete")}
                className="absolute top-1 right-1 size-2 rounded-full bg-warn"
              />
            )}
          </IconLink>
        </>
      }
    >
      <div className="flex shrink-0 items-center gap-2 pl-1">
        <img src="/logo.svg" alt="" className="size-7" />
        <span className="text-base font-medium">{BRAND.name}</span>
      </div>
      <InputGroup className="ml-3 max-w-80">
        <InputGroupAddon>
          <Search aria-hidden />
        </InputGroupAddon>
        <InputGroupInput
          ref={searchRef}
          type="search"
          value={query}
          onChange={(event) => onQueryChange(event.target.value)}
          aria-label={t("dashboard:lobby_search_label")}
          aria-keyshortcuts="Meta+K Control+K"
          placeholder={t("dashboard:lobby_search_placeholder")}
          autoComplete="off"
          spellCheck={false}
        />
        <InputGroupAddon align="inline-end">
          <InputGroupText aria-hidden>{t("dashboard:lobby_search_kbd")}</InputGroupText>
        </InputGroupAddon>
      </InputGroup>
    </PageHeader>
  );
}

function IconLink({
  href,
  label,
  onboardingAnchor,
  children,
}: {
  href: string;
  label: string;
  onboardingAnchor?: string;
  children: ReactNode;
}) {
  return (
    <Tooltip>
      <TooltipTrigger
        render={
          <Link
            href={href}
            aria-label={label}
            data-onboarding={onboardingAnchor}
            className={cn(buttonVariants({ variant: "ghost", size: "icon" }), "relative")}
          />
        }
      >
        {children}
      </TooltipTrigger>
      <TooltipContent>{label}</TooltipContent>
    </Tooltip>
  );
}
