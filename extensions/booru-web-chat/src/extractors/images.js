import { extractRedditImage } from "./reddit.js";

export { extractRedditImage };

/**
 * @param {Document} doc
 * @param {string | null | undefined} url
 * @returns {string | null}
 */
function toAbsoluteUrl(doc, url) {
  if (!url) return null;
  try {
    return new URL(url, doc.location?.href || undefined).href;
  } catch {
    return null;
  }
}

/**
 * @param {string} url
 * @returns {boolean}
 */
function isLowResUrl(url) {
  return /\/(preview|sample)\//i.test(url);
}

/**
 * @param {Array<string | null | undefined>} candidates
 * @returns {string | null}
 */
function pickBestUrl(candidates) {
  const valid = candidates.filter((value) => typeof value === "string" && value.length > 0);
  const full = valid.filter((url) => !isLowResUrl(url));
  return full[0] || valid[0] || null;
}

/**
 * @param {Document} doc
 * @returns {string | null}
 */
export function extractE621Image(doc) {
  const og = doc.querySelector('meta[property="og:image"]')?.getAttribute("content");
  const download = doc.querySelector("a.ptbr-etc-download")?.getAttribute("href");
  const img = doc.querySelector("#image")?.getAttribute("src");

  return pickBestUrl([
    toAbsoluteUrl(doc, og),
    toAbsoluteUrl(doc, download),
    toAbsoluteUrl(doc, img),
  ]);
}

/**
 * @param {Document} doc
 * @returns {string | null}
 */
export function extractRule34Image(doc) {
  const img = doc.querySelector("#image")?.getAttribute("src");
  return toAbsoluteUrl(doc, img);
}

/**
 * @param {Document} doc
 * @returns {string | null}
 */
export function extractDerpibooruImage(doc) {
  let src =
    doc.querySelector("#image-display")?.getAttribute("src") ||
    doc.querySelector("picture img")?.getAttribute("src");
  if (!src) return null;

  let url = toAbsoluteUrl(doc, src);
  if (url && /\/medium\./i.test(url)) {
    url = url.replace(/\/medium\./i, "/full.");
  }
  return url;
}

/**
 * @param {Document} doc
 * @returns {string | null}
 */
export function extractGenericImage(doc) {
  const img =
    doc.querySelector("#image")?.getAttribute("src") ||
    doc.querySelector("picture img")?.getAttribute("src");
  return toAbsoluteUrl(doc, img);
}

/** @type {{ match: RegExp, extract: (doc: Document) => string | null }[]} */
const SITE_IMAGE_EXTRACTORS = [
  { match: /(^|\.)e621\.net$|(^|\.)e926\.net$/, extract: extractE621Image },
  { match: /(^|\.)rule34\.xxx$/, extract: extractRule34Image },
  {
    match: /(^|\.)derpibooru\.org$|(^|\.)tantabus\.ai$/,
    extract: extractDerpibooruImage,
  },
  { match: /(^|\.)reddit\.com$/, extract: extractRedditImage },
];

/**
 * @param {Document} doc
 * @returns {string | null}
 */
export function extractImageUrl(doc) {
  const host = doc.location?.hostname ?? "";

  for (const { match, extract } of SITE_IMAGE_EXTRACTORS) {
    if (match.test(host)) {
      const url = extract(doc);
      if (url) return url;
    }
  }

  return extractGenericImage(doc);
}
