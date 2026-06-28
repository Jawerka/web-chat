/**
 * @param {Document} doc
 * @returns {string[]}
 */
export function extractDerpibooru(doc) {
  const hidden = doc.querySelector("#tags-form_old_tag_input");
  if (hidden?.value) {
    return hidden.value.split(",").map((t) => t.trim()).filter(Boolean);
  }

  const container = doc.querySelector(".block__content .tag-list, .tag-list");
  if (!container) return [];

  return [...container.querySelectorAll(".tag[data-tag-name]")].map((el) =>
    el.getAttribute("data-tag-name")
  );
}
