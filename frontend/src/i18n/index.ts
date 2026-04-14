
import i18n from 'i18next';
import { initReactI18next } from 'react-i18next';
import LanguageDetector from 'i18next-browser-languagedetector';
import { voidCall } from '@/utils/async';

import enCommon from './en/common';
import enAuth from './en/auth';
import enDashboard from './en/dashboard';
import enErrors from './en/errors';

import zhCommon from './zh/common';
import zhAuth from './zh/auth';
import zhDashboard from './zh/dashboard';
import zhErrors from './zh/errors';

const resources = {
  en: {
    common: enCommon,
    auth: enAuth,
    dashboard: enDashboard,
    errors: enErrors,
  },
  zh: {
    common: zhCommon,
    auth: zhAuth,
    dashboard: zhDashboard,
    errors: zhErrors,
  },
};

voidCall(i18n
  .use(LanguageDetector)
  .use(initReactI18next)
  .init({
    resources,
    fallbackLng: 'zh',
    debug: false,
    interpolation: {
      escapeValue: false,
    },
    // Use 'common' as the default namespace
    defaultNS: 'common',
    ns: ['common', 'auth', 'dashboard', 'errors'],
  }));

export default i18n;
