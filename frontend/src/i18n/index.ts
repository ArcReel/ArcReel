
import i18n from 'i18next';
import { initReactI18next } from 'react-i18next';
import LanguageDetector from 'i18next-browser-languagedetector';
import { voidCall } from '@/utils/async';

import enCommon from './en/common';
import enAuth from './en/auth';
import enDashboard from './en/dashboard';
import enErrors from './en/errors';
import enTemplates from './en/templates';
import enAssets from './en/assets';

import zhCommon from './zh/common';
import zhAuth from './zh/auth';
import zhDashboard from './zh/dashboard';
import zhErrors from './zh/errors';
import zhTemplates from './zh/templates';
import zhAssets from './zh/assets';

import viCommon from './vi/common';
import viAuth from './vi/auth';
import viDashboard from './vi/dashboard';
import viErrors from './vi/errors';
import viTemplates from './vi/templates';
import viAssets from './vi/assets';

export const SUPPORTED_LANGUAGES = ['zh', 'en', 'vi'] as const;
export type SupportedLanguage = typeof SUPPORTED_LANGUAGES[number];

export const LANGUAGE_DISPLAY_LABELS: Record<SupportedLanguage, string> = {
  zh: '中文',
  en: 'English',
  vi: 'Tiếng Việt',
};

const resources = {
  en: {
    common: enCommon,
    auth: enAuth,
    dashboard: enDashboard,
    errors: enErrors,
    templates: enTemplates,
    assets: enAssets,
  },
  zh: {
    common: zhCommon,
    auth: zhAuth,
    dashboard: zhDashboard,
    errors: zhErrors,
    templates: zhTemplates,
    assets: zhAssets,
  },
  vi: {
    common: viCommon,
    auth: viAuth,
    dashboard: viDashboard,
    errors: viErrors,
    templates: viTemplates,
    assets: viAssets,
  },
};

voidCall(i18n
  .use(LanguageDetector)
  .use(initReactI18next)
  .init({
    resources,
    fallbackLng: 'zh',
    supportedLngs: SUPPORTED_LANGUAGES as unknown as string[],
    debug: false,
    interpolation: {
      escapeValue: false,
    },
    // Use 'common' as the default namespace
    defaultNS: 'common',
    ns: ['common', 'auth', 'dashboard', 'errors', 'templates', 'assets'],
  }));

export default i18n;
