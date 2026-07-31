// @ts-check
import { defineConfig } from 'astro/config';
import mdx from '@astrojs/mdx';
import sitemap from '@astrojs/sitemap';
import tailwind from '@tailwindcss/vite';
import { remarkCitations } from './src/plugins/remark-citations.mjs';

// Update once the domain is registered; sitemap and canonical URLs depend on it.
const SITE = process.env.SITE_URL || 'https://golfodulce.org';

export default defineConfig({
  site: SITE,
  trailingSlash: 'never',

  i18n: {
    locales: ['en', 'es'],
    defaultLocale: 'en',
    routing: {
      // Both languages are first-class; the English audience is international,
      // the Spanish audience is local and academic. Neither gets the bare root.
      prefixDefaultLocale: true,
    },
  },

  markdown: {
    remarkPlugins: [remarkCitations],
    shikiConfig: { theme: 'github-dark-dimmed', wrap: true },
  },

  integrations: [mdx(), sitemap({ i18n: { defaultLocale: 'en', locales: { en: 'en', es: 'es' } } })],

  vite: {
    plugins: [tailwind()],
  },
});
