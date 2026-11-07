import i18n from 'i18next';
import { initReactI18next } from 'react-i18next';
import LanguageDetector from 'i18next-browser-languagedetector';
import resourcesToBackend from 'i18next-resources-to-backend';
import { BRAND } from '@/branding';

// Load i18n namespaces on demand:
// Vite import.meta.glob creates a separate chunk for each (lang, ns) file at build time;
// i18next loads it asynchronously at runtime. Resources remain .ts (retaining the satisfies Record schema constraint), not JSON.
const loaders = import.meta.glob<{ default: Record<string, string> }>(
  './{en,zh,vi,ru}/*.ts',
);

function pathFor(lang: string, ns: string): string {
  return `./${lang}/${ns}.ts`;
}

export const SUPPORTED_LANGUAGES = ['zh', 'en', 'vi', 'ru'] as const;
export type SupportedLanguage = typeof SUPPORTED_LANGUAGES[number];

export const LANGUAGE_DISPLAY_LABELS: Record<SupportedLanguage, string> = {
  zh: '中文',
  en: 'English',
  vi: 'Tiếng Việt',
  ru: 'Русский',
};

const I18N_NAMESPACES = [
  'common',
  'auth',
  'dashboard',
  'errors',
  'events',
  'templates',
  'assets',
  'onboarding',
  'workflow',
] as const;

// Replace every [[brand]] placeholder in a loaded namespace with the current
// brand name. Done inside the resourcesToBackend loader so it composes with
// on-demand chunk loading. We use [[...]] rather than i18next's
// native {{...}} so the value is not treated as a runtime variable (which
// would force every t() call site to pass { brand }).
function applyBrandPlaceholders(value: unknown): unknown {
  if (typeof value === 'string') {
    // Function replacer avoids `$`-sequences in BRAND.name (e.g. "Product$1")
    // being interpreted as String.prototype.replace patterns.
    return value.replace(/\[\[\s*brand\s*\]\]/g, () => BRAND.name);
  }
  if (Array.isArray(value)) {
    return value.map(applyBrandPlaceholders);
  }
  if (value && typeof value === 'object') {
    const out: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(value as Record<string, unknown>)) {
      out[k] = applyBrandPlaceholders(v);
    }
    return out;
  }
  return value;
}

// Return the init Promise; callers (main.tsx / test setup) await it before rendering to prevent keys flashing on the first screen.
export const i18nReady = i18n
  .use(
    resourcesToBackend(async (lang: string, ns: string) => {
      const loader = loaders[pathFor(lang, ns)];
      if (!loader) {
        // LanguageDetector may resolve regional BCP47 codes such as zh-CN / en-GB;
        // i18next first requests the full code, then falls back to zh/en. This is expected,
        // so it must not become an exception. Return an empty object to let i18next follow its fallback chain.
        console.warn(`i18n: no resource for ${pathFor(lang, ns)}, falling back`);
        return {};
      }
      const mod = await loader();
      return applyBrandPlaceholders(mod.default) as Record<string, string>;
    }),
  )
  .use(LanguageDetector)
  .use(initReactI18next)
  .init({
    fallbackLng: 'zh',
    supportedLngs: SUPPORTED_LANGUAGES,
    debug: false,
    interpolation: { escapeValue: false },
    defaultNS: 'common',
    ns: I18N_NAMESPACES,
    partialBundledLanguages: true,
    react: { useSuspense: false },
  });

export default i18n;
