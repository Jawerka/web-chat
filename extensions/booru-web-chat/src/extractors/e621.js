/**
 * @param {Document} doc
 * @returns {string[]}
 */
export function extractE621(doc) {
  const container = doc.querySelector("#tag-list");
  if (!container) return [];

  return [...container.querySelectorAll(".tag-list-item[data-name]")].map((el) =>
    decodeURIComponent(el.getAttribute("data-name"))
  );
}
