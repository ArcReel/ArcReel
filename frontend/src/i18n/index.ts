import i18n from 'i18next';
import { initReactI18next } from 'react-i18next';
import LanguageDetector from 'i18next-browser-languagedetector';
import resourcesToBackend from 'i18next-resources-to-backend';
import { BRAND } from '@/branding';

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

export const i18nReady = i18n
  .use(
    resourcesToBackend(async (lang: string, ns: string) => {
      const loader = loaders[pathFor(lang, ns)];
      if (!loader) {
        // LanguageDetector 可能解析出 zh-CN / en-GB 等带区域的 BCP47 代码，
        // i18next 会先按完整代码请求一次再 fallback 到 zh/en。这是预期路径，
        // 不应该升级成异常。返回空对象让 i18next 走 fallback 链。
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
