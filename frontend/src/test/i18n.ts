import i18n, { i18nReady } from "@/i18n";

await i18nReady;
await i18n.changeLanguage("zh");

export default i18n;
