import i18n from 'i18next';
import { initReactI18next } from 'react-i18next';
import LanguageDetector from 'i18next-browser-languagedetector';
import resourcesToBackend from 'i18next-resources-to-backend';

// 按需加载 i18n namespace（issue #489）：
// Vite import.meta.glob 在编译期为每个 (lang, ns) 文件生成独立 chunk；运行时由
// i18next 异步 load。资源仍是 .ts（保留 satisfies Record schema 锁），不是 JSON。
const loaders = import.meta.glob<{ default: Record<string, string> }>(
  './{en,zh,vi}/*.ts',
);

function pathFor(lang: string, ns: string): string {
  return `./${lang}/${ns}.ts`;
}

export const SUPPORTED_LANGUAGES = ['zh', 'en', 'vi'] as const;
export type SupportedLanguage = typeof SUPPORTED_LANGUAGES[number];

export const LANGUAGE_DISPLAY_LABELS: Record<SupportedLanguage, string> = {
  zh: '中文',
  en: 'English',
  vi: 'Tiếng Việt',
};

export const I18N_NAMESPACES = [
  'common',
  'auth',
  'dashboard',
  'errors',
  'templates',
  'assets',
] as const;

// 返回 init Promise，调用方（main.tsx / test setup）await 后再 render，避免首屏闪 key。
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
      return mod.default;
    }),
  )
  .use(LanguageDetector)
  .use(initReactI18next)
  .init({
    fallbackLng: 'zh',
    supportedLngs: SUPPORTED_LANGUAGES as unknown as string[],
    debug: false,
    interpolation: { escapeValue: false },
    defaultNS: 'common',
    ns: I18N_NAMESPACES as unknown as string[],
    partialBundledLanguages: true,
    react: { useSuspense: false },
  });

export default i18n;
