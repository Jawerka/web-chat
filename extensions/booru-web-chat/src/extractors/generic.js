const CONTAINER_SELECTORS = ["#tag-list", "#tag-sidebar", ".tag-list"];

const TAG_SELECTORS = [
  "[data-name]",
  "[data-tag-name]",
  'li.tag a[href*="tags="]',
  ".tag-list-item .tag-list-name",
  ".tag__name",
];

/**
 * @param {Document} doc
 * @returns {string[]}
 */
export function extractGeneric(doc) {
  for (const selector of CONTAINER_SELECTORS) {
    const container = doc.querySelector(selector);
    if (!container) continue;

    const tags = extractFromContainer(container);
    if (tags.length > 0) return tags;
  }

  return [];
}

/**
 * @param {ParentNode} container
 * @returns {string[]}
 */
function extractFromContainer(container) {
  const tags = [];

  for (const el of container.querySelectorAll("[data-name]")) {
    const name = el.getAttribute("data-name");
    if (name) tags.push(decodeURIComponent(name));
  }
  if (tags.length > 0) return tags;

  for (const el of container.querySelectorAll("[data-tag-name]")) {
    const name = el.getAttribute("data-tag-name");
    if (name) tags.push(name);
  }
  if (tags.length > 0) return tags;

  for (const el of container.querySelectorAll('li.tag a[href*="tags="]')) {
    const text = el.textContent.trim();
    if (text && text !== "?") tags.push(text);
  }
  if (tags.length > 0) return tags;

  for (const selector of TAG_SELECTORS.slice(3)) {
    for (const el of container.querySelectorAll(selector)) {
      const text = el.textContent.trim();
      if (text) tags.push(text);
    }
    if (tags.length > 0) return tags;
  }

  return tags;
}
