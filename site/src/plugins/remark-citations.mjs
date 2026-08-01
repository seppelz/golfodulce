/**
 * Turns `[@source-id]` in MDX prose into a numbered, linked reference.
 *
 * Numbering is per-document and follows order of first appearance, the way a journal
 * article numbers its references. The ordered list of ids used is written back to
 * frontmatter as `citations`, so the page layout can render a matching reference list
 * without re-parsing the body.
 *
 * Multiple ids in one bracket are separated by semicolons: `[@smith-2001-x; @lee-2010-y]`.
 */

import { visit } from 'unist-util-visit';

const CITE = /\[@([^\]]+)\]/g;

export function remarkCitations() {
  return (tree, file) => {
    /** id -> reference number, in order of first appearance */
    const order = new Map();
    /** id -> number of times already rendered on this page, so anchor ids stay unique */
    const seenCount = new Map();

    visit(tree, 'text', (node, index, parent) => {
      if (!parent || index === null || !CITE.test(node.value)) return;
      CITE.lastIndex = 0;

      const children = [];
      let cursor = 0;
      let match;

      while ((match = CITE.exec(node.value)) !== null) {
        if (match.index > cursor) {
          children.push({ type: 'text', value: node.value.slice(cursor, match.index) });
        }

        const ids = match[1].split(';').map((s) => s.trim().replace(/^@/, '')).filter(Boolean);

        children.push({ type: 'html', value: '<sup class="citation-group">' });
        ids.forEach((id, i) => {
          if (!order.has(id)) order.set(id, order.size + 1);
          const n = order.get(id);

          // Each citation of the same source needs its own DOM id (duplicate ids are
          // invalid HTML and break in-page navigation), but the reference list's
          // "back to text" link only needs to reach one of them — the first.
          const occurrence = (seenCount.get(id) ?? 0) + 1;
          seenCount.set(id, occurrence);
          const anchorId = occurrence === 1 ? `cite-${id}-${n}` : `cite-${id}-${n}-${occurrence}`;

          if (i > 0) children.push({ type: 'html', value: ',' });
          children.push({
            type: 'html',
            value: `<a href="#ref-${id}" id="${anchorId}" class="citation" data-source-id="${id}" title="${id}">${n}</a>`,
          });
        });
        children.push({ type: 'html', value: '</sup>' });

        cursor = match.index + match[0].length;
      }

      if (cursor < node.value.length) {
        children.push({ type: 'text', value: node.value.slice(cursor) });
      }

      parent.children.splice(index, 1, ...children);
      return index + children.length;
    });

    // Hand the ordered ids to the layout via frontmatter.
    const fm = (file.data.astro ??= {}).frontmatter ??= {};
    fm.citations = [...order.keys()];
  };
}

export default remarkCitations;
