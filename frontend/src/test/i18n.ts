import i18n, { i18nReady } from "../i18n/index";

await i18nReady;
if (i18n.language !== "zh") await i18n.changeLanguage("zh");

export * from "../i18n/index";
export default i18n;
