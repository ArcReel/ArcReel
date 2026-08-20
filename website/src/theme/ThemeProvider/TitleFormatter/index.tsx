import { type ComponentProps, type ReactNode } from "react";
import { TitleFormatterProvider } from "@docusaurus/theme-common/internal";
import type { Props } from "@theme/ThemeProvider/TitleFormatter";
import { getSiteTitle } from "../../../i18n/siteMetadata";

type Formatter = ComponentProps<typeof TitleFormatterProvider>["formatter"];

const formatter: Formatter = (params) => params.defaultFormatter({ ...params, siteTitle: getSiteTitle() });

export default function ThemeProviderTitleFormatter({ children }: Props): ReactNode {
  return <TitleFormatterProvider formatter={formatter}>{children}</TitleFormatterProvider>;
}
