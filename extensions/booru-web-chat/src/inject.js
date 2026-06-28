import { extractTags, extractImageUrl } from "./extractors/index.js";
import { formatTags } from "./format.js";

function extractPage() {
  const tags = extractTags(document);
  const imageUrl = extractImageUrl(document);

  if (tags.length === 0) {
    return { ok: false, error: "No tags found on this page" };
  }
  if (!imageUrl) {
    return { ok: false, error: "No image found on this page" };
  }

  const text = formatTags(tags);
  return {
    ok: true,
    count: tags.length,
    text,
    tags,
    imageUrl,
    pageUrl: document.location?.href ?? "",
  };
}

globalThis.__booruWebChat = { extractPage, formatTags };
