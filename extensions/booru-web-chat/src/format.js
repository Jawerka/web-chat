/**
 * @param {string[]} tags
 * @returns {string[]}
 */
export function sortTags(tags) {
  return [...new Set(tags.map((t) => t.trim()).filter(Boolean))].sort((a, b) =>
    a.localeCompare(b, undefined, { sensitivity: "base" })
  );
}

/**
 * @param {string[]} tags
 * @returns {string}
 */
export function formatTags(tags) {
  return sortTags(tags).join(", ");
}
