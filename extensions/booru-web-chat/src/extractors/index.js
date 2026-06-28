import { extractE621 } from "./e621.js";
import { extractRule34 } from "./rule34.js";
import { extractDerpibooru } from "./derpibooru.js";
import { extractReddit } from "./reddit.js";
import { extractGeneric } from "./generic.js";
import {
  extractImageUrl,
  extractE621Image,
  extractRule34Image,
  extractDerpibooruImage,
  extractRedditImage,
  extractGenericImage,
} from "./images.js";

/** @type {{ match: RegExp, extract: (doc: Document) => string[] }[]} */
const SITE_EXTRACTORS = [
  { match: /(^|\.)e621\.net$|(^|\.)e926\.net$/, extract: extractE621 },
  { match: /(^|\.)rule34\.xxx$/, extract: extractRule34 },
  { match: /(^|\.)derpibooru\.org$|(^|\.)tantabus\.ai$/, extract: extractDerpibooru },
  { match: /(^|\.)reddit\.com$/, extract: extractReddit },
];

/**
 * @param {Document} doc
 * @returns {string[]}
 */
export function extractTags(doc) {
  const host = doc.location?.hostname ?? "";

  for (const { match, extract } of SITE_EXTRACTORS) {
    if (match.test(host)) {
      const tags = extract(doc);
      if (tags.length > 0) return tags;
    }
  }

  return extractGeneric(doc);
}

export {
  extractE621,
  extractRule34,
  extractDerpibooru,
  extractReddit,
  extractGeneric,
  extractImageUrl,
  extractE621Image,
  extractRule34Image,
  extractDerpibooruImage,
  extractRedditImage,
  extractGenericImage,
};
