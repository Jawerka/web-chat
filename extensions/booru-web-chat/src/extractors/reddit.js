/**
 * @param {string | null | undefined} srcset
 * @returns {string | null}
 */
function pickLargestSrcsetUrl(srcset) {
  if (!srcset) return null;

  let bestUrl = null;
  let bestWidth = 0;

  for (const entry of srcset.split(",")) {
    const trimmed = entry.trim();
    const match = trimmed.match(/^(\S+)\s+(\d+)w$/);
    if (!match) continue;

    const width = Number.parseInt(match[2], 10);
    if (width > bestWidth) {
      bestWidth = width;
      bestUrl = match[1];
    }
  }

  return bestUrl;
}

/**
 * @param {Document} doc
 * @returns {string[]}
 */
export function extractReddit(doc) {
  const post = doc.querySelector("shreddit-post");
  const tags = [];

  const title =
    post?.getAttribute("post-title")?.trim() ||
    doc.querySelector('h1[slot="title"]')?.textContent?.trim() ||
    doc.querySelector('[slot="title"]')?.textContent?.trim() ||
    doc.querySelector("h1")?.textContent?.trim();
  if (title) tags.push(title);

  const subreddit = post?.getAttribute("subreddit-prefixed-name")?.trim();
  if (subreddit) tags.push(subreddit);

  const flair = post?.getAttribute("flair-text")?.trim();
  if (flair) tags.push(flair);

  return tags;
}

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
 * @param {Document} doc
 * @returns {string | null}
 */
export function extractRedditImage(doc) {
  const post = doc.querySelector("shreddit-post");
  const contentHref = post?.getAttribute("content-href");
  if (contentHref && /\.(jpe?g|png|gif|webp)(\?|$)/i.test(contentHref)) {
    return toAbsoluteUrl(doc, contentHref);
  }

  const postImage = doc.querySelector("#post-image");
  if (postImage) {
    const fromSrcset = pickLargestSrcsetUrl(postImage.getAttribute("srcset"));
    const src = fromSrcset || postImage.getAttribute("src");
    const url = toAbsoluteUrl(doc, src);
    if (url) return url;
  }

  const og = doc.querySelector('meta[property="og:image"]')?.getAttribute("content");
  return toAbsoluteUrl(doc, og);
}
