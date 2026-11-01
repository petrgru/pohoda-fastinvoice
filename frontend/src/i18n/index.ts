import i18n from "i18next"
import LanguageDetector from "i18next-browser-languagedetector"
import { initReactI18next } from "react-i18next"

import cs from "./locales/cs.json"
import de from "./locales/de.json"
import en from "./locales/en.json"

export const SUPPORTED_LANGUAGES = [
  { code: "cs", label: "Čeština" },
  { code: "en", label: "English" },
  { code: "de", label: "Deutsch" },
] as const

export const LANGUAGE_STORAGE_KEY = "app_language"

i18n
  .use(LanguageDetector)
  .use(initReactI18next)
  .init({
    resources: {
      cs: { translation: cs },
      en: { translation: en },
      de: { translation: de },
    },
    fallbackLng: "cs",
    supportedLngs: ["cs", "en", "de"],
    // the saved choice wins; without one the UI starts in Czech
    detection: {
      order: ["localStorage"],
      lookupLocalStorage: LANGUAGE_STORAGE_KEY,
      caches: ["localStorage"],
    },
    interpolation: { escapeValue: false },
  })

/** BCP-47 tag matching the active language, for Intl / toLocaleString formatting. */
export const intlLocale = () =>
  ({ cs: "cs-CZ", en: "en-GB", de: "de-DE" })[
    (i18n.resolvedLanguage ?? "cs") as "cs" | "en" | "de"
  ]

export default i18n
