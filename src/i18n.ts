// UI language: English, or Russian when the Steam client itself runs in Russian.

function steamLanguage(): string {
  try {
    const l = (window as any).LocalizationManager?.m_rgLocalesToUse?.[0];
    if (typeof l === "string") return l;
  } catch (_) {
    /* ignore */
  }
  return "en";
}

export function isRussian(): boolean {
  return steamLanguage().toLowerCase().startsWith("ru");
}

/** The text in the UI language. */
export function t(en: string, ru: string): string {
  return isRussian() ? ru : en;
}

/** For tables of [english, russian] pairs. */
export function tr(pair: readonly [string, string] | undefined): string {
  return pair ? t(pair[0], pair[1]) : "";
}
