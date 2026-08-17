import { type ReactNode } from "react";
import Head from "@docusaurus/Head";
import OriginalSiteMetadata from "@theme-original/SiteMetadata";
import { getSiteTagline, getSiteTitle } from "../../i18n/siteMetadata";

export default function SiteMetadata(): ReactNode {
  const title = getSiteTitle();
  const tagline = getSiteTagline();

  return (
    <>
      <OriginalSiteMetadata />
      <Head>
        <title>{title}</title>
        <meta property="og:title" content={title} />
        <meta name="description" content={tagline} />
        <meta property="og:description" content={tagline} />
      </Head>
    </>
  );
}
