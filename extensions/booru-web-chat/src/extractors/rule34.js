/**
 * @param {Document} doc
 * @returns {string[]}
 */
export function extractRule34(doc) {
  const container = doc.querySelector("#tag-sidebar");
  if (!container) return [];

  const tags = [];
  for (const item of container.querySelectorAll("li.tag")) {
    const link = item.querySelector('a[href*="page=post"][href*="tags="]');
    if (link) {
      tags.push(link.textContent.trim());
    }
  }
  return tags;
}
