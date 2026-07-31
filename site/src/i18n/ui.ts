/**
 * UI strings. Content lives in MDX; this covers only chrome.
 *
 * Spanish is not a courtesy translation here — the primary local and academic
 * audience (CIMAR/UCR, INCOPESCA, local operators) reads Spanish first.
 */

export const LOCALES = ['en', 'es'] as const;
export type Locale = (typeof LOCALES)[number];
export const DEFAULT_LOCALE: Locale = 'en';

export const localeNames: Record<Locale, string> = {
  en: 'English',
  es: 'Español',
};

export const ui = {
  en: {
    'site.title': 'Golfo Dulce',
    'site.tagline': 'A tropical anoxic basin, documented',
    'site.description':
      'An open, sourced knowledge base on the Golfo Dulce, Costa Rica — its oceanography, bathymetry, geology, ecology and history, and an honest register of what remains unknown.',

    'nav.overview': 'The Golfo Dulce',
    'nav.oceanography': 'Oceanography',
    'nav.bathymetry': 'Bathymetry',
    'nav.geology': 'Geology',
    'nav.life': 'Life',
    'nav.people': 'People',
    'nav.conservation': 'Conservation',
    'nav.gaps': 'What we don’t know',
    'nav.sources': 'Sources',
    'nav.about': 'About',

    'meta.updated': 'Last updated',
    'meta.sources_count': 'sources cited',
    'meta.on_this_page': 'On this page',

    'refs.heading': 'References',
    'refs.empty': 'No sources are cited on this page yet.',

    'sources.heading': 'Annotated bibliography',
    'sources.filter_topic': 'Filter by topic',
    'sources.filter_access': 'Access',
    'sources.filter_language': 'Language',
    'sources.open': 'Open access',
    'sources.paywalled': 'Paywalled',
    'sources.request': 'On request',
    'sources.unknown': 'Unknown',
    'sources.contributes': 'What it contributes',
    'sources.data': 'Data availability',
    'sources.retrieved': 'Retrieved',

    'gaps.heading': 'What we don’t know',
    'gaps.intro':
      'A structured register of open questions about the Golfo Dulce. Each entry states what is currently known, why the gap exists, and what it would take to close it.',
    'gaps.question': 'The question',
    'gaps.known': 'What is known',
    'gaps.why_gap': 'Why the gap exists',
    'gaps.to_close': 'What it would take',
    'gaps.why_matters': 'Why it matters',
    'gaps.cost': 'Cost band',
    'gaps.effort': 'Effort',
    'gaps.equipment': 'Equipment',
    'gaps.priority': 'Priority',
    'gaps.confidence': 'Confidence this is genuinely open',

    'map.title': 'Depth map',
    'map.coverage_toggle': 'Show data coverage',
    'map.coverage_help':
      'Where the depth surface rests on real soundings, and where it is interpolation.',
    'map.depth': 'Depth',
    'map.no_data': 'No measured data',

    'uncertainty.label': 'Uncertain or contested',

    'contact.heading': 'Corrections and contributions',
    'contact.intro':
      'If you know this water — as a researcher, a captain, a fisher, or a resident — and something here is wrong, please tell us. Corrections are the fastest route to accuracy.',

    'footer.licence': 'Text and data licensing',
    'footer.methodology': 'Methodology',
    'skip': 'Skip to content',
  },

  es: {
    'site.title': 'Golfo Dulce',
    'site.tagline': 'Una cuenca anóxica tropical, documentada',
    'site.description':
      'Una base de conocimiento abierta y documentada sobre el Golfo Dulce, Costa Rica — su oceanografía, batimetría, geología, ecología e historia, y un registro honesto de lo que aún se desconoce.',

    'nav.overview': 'El Golfo Dulce',
    'nav.oceanography': 'Oceanografía',
    'nav.bathymetry': 'Batimetría',
    'nav.geology': 'Geología',
    'nav.life': 'Vida',
    'nav.people': 'Gente',
    'nav.conservation': 'Conservación',
    'nav.gaps': 'Lo que no sabemos',
    'nav.sources': 'Fuentes',
    'nav.about': 'Acerca de',

    'meta.updated': 'Última actualización',
    'meta.sources_count': 'fuentes citadas',
    'meta.on_this_page': 'En esta página',

    'refs.heading': 'Referencias',
    'refs.empty': 'Aún no se citan fuentes en esta página.',

    'sources.heading': 'Bibliografía anotada',
    'sources.filter_topic': 'Filtrar por tema',
    'sources.filter_access': 'Acceso',
    'sources.filter_language': 'Idioma',
    'sources.open': 'Acceso abierto',
    'sources.paywalled': 'De pago',
    'sources.request': 'Bajo solicitud',
    'sources.unknown': 'Desconocido',
    'sources.contributes': 'Qué aporta',
    'sources.data': 'Disponibilidad de datos',
    'sources.retrieved': 'Consultado',

    'gaps.heading': 'Lo que no sabemos',
    'gaps.intro':
      'Un registro estructurado de preguntas abiertas sobre el Golfo Dulce. Cada entrada indica qué se sabe actualmente, por qué existe el vacío y qué haría falta para cerrarlo.',
    'gaps.question': 'La pregunta',
    'gaps.known': 'Qué se sabe',
    'gaps.why_gap': 'Por qué existe el vacío',
    'gaps.to_close': 'Qué haría falta',
    'gaps.why_matters': 'Por qué importa',
    'gaps.cost': 'Rango de coste',
    'gaps.effort': 'Esfuerzo',
    'gaps.equipment': 'Equipamiento',
    'gaps.priority': 'Prioridad',
    'gaps.confidence': 'Confianza en que sigue abierta',

    'map.title': 'Mapa de profundidad',
    'map.coverage_toggle': 'Mostrar cobertura de datos',
    'map.coverage_help':
      'Dónde la superficie de profundidad se apoya en sondeos reales y dónde es interpolación.',
    'map.depth': 'Profundidad',
    'map.no_data': 'Sin datos medidos',

    'uncertainty.label': 'Incierto o debatido',

    'contact.heading': 'Correcciones y aportes',
    'contact.intro':
      'Si usted conoce estas aguas — como investigador, capitán, pescador o vecino — y algo aquí está equivocado, por favor díganoslo. Las correcciones son la vía más rápida hacia la exactitud.',

    'footer.licence': 'Licencia de textos y datos',
    'footer.methodology': 'Metodología',
    'skip': 'Saltar al contenido',
  },
} as const;

export type UIKey = keyof (typeof ui)['en'];

/** Returns a translator for a locale, falling back to English for any missing key. */
export function useTranslations(locale: Locale) {
  return function t(key: UIKey): string {
    return (ui[locale] as Record<string, string>)[key] ?? ui.en[key];
  };
}

export function isLocale(value: string): value is Locale {
  return (LOCALES as readonly string[]).includes(value);
}
