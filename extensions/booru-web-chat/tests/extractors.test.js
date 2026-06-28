import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { JSDOM } from "jsdom";
import { describe, expect, it } from "vitest";
import {
  extractDerpibooru,
  extractE621,
  extractRule34,
  extractTags,
  extractImageUrl,
  extractE621Image,
  extractRule34Image,
  extractDerpibooruImage,
} from "../src/extractors/index.js";
import { formatTags } from "../src/format.js";

const __dirname = dirname(fileURLToPath(import.meta.url));
const refDir = join(__dirname, "..", "..", "..", "tag-copy", "ref");

/** @param {string} filename @param {string} url */
function loadRefWithUrl(filename, url) {
  const html = readFileSync(join(refDir, filename), "utf8");
  const dom = new JSDOM(html, { url });
  return dom.window.document;
}

describe("e621 tags", () => {
  const doc = loadRefWithUrl("#3227190 - e621.html", "https://e621.net/posts/3227190");

  it("extracts tags from #tag-list", () => {
    const tags = extractE621(doc);
    expect(tags.length).toBeGreaterThan(10);
    expect(tags).toContain("solterv");
    expect(tags).toContain("anthro");
  });

  it("uses site-specific extractor via extractTags", () => {
    expect(extractTags(doc)).toContain("solterv");
  });
});

describe("e621 image", () => {
  const doc = loadRefWithUrl("#3227190 - e621.html", "https://e621.net/posts/3227190");

  it("prefers full og:image over sample #image", () => {
    const url = extractE621Image(doc);
    expect(url).toMatch(/^https:\/\/static1\.e621\.net\/data\/54\/24\//);
    expect(url).not.toMatch(/\/sample\//);
    expect(url).not.toMatch(/\/preview\//);
  });

  it("extractImageUrl resolves for e621 host", () => {
    expect(extractImageUrl(doc)).toBe(extractE621Image(doc));
  });
});

describe("rule34.xxx", () => {
  const doc = loadRefWithUrl(
    "Rule 34 - animated arkihamatnight cum in pussy cum inside doggy style from behind holding hair holding tail prone bone rigid3d sfx sex from behind tagme that1lewddude vanilla flavor video xray _ 17715786 _.html",
    "https://rule34.xxx/index.php?page=post&s=view&id=17715786"
  );

  it("extracts tags from #tag-sidebar", () => {
    const tags = extractRule34(doc);
    expect(tags).toEqual(
      expect.arrayContaining(["arkihamatnight", "animated", "tagme", "xray"])
    );
  });

  it("extracts #image src from post markup", () => {
    const imageDoc = new JSDOM(
      '<html><body><img id="image" src="https://wimg.rule34.xxx//images/5368/45e3f16f63afce1a5ede050602694226.jpeg?17935288" width="1171" height="1635"></body></html>',
      { url: "https://rule34.xxx/index.php?page=post&s=view&id=17935288" }
    ).window.document;
    const url = extractRule34Image(imageDoc);
    expect(url).toMatch(/^https:\/\/wimg\.rule34\.xxx\//);
    expect(extractImageUrl(imageDoc)).toBe(url);
  });
});

describe("derpibooru", () => {
  const doc = loadRefWithUrl(
    "#3832293 - safe, artist_oofycolorful, oc, oc only, pegasus, unicorn, semi-anthro, beret, bow, bracelet, clothes, dress, duo, eyebrows, eyebrows visible through hair, female, hair bow, hat, horn, jewelry, leg warmers, legwear, open m.html",
    "https://derpibooru.org/images/3832293"
  );

  it("extracts tags from hidden input or tag-list", () => {
    const tags = extractDerpibooru(doc);
    expect(tags.length).toBeGreaterThan(20);
    expect(tags).toContain("safe");
    expect(tags).toContain("artist:oofycolorful");
  });

  it("upgrades medium image URL to full when needed", () => {
    const docWithImage = new JSDOM(
      '<html><body><picture><img id="image-display" src="https://derpicdn.net/img/2026/6/28/3844783/medium.jpg"></picture></body></html>',
      { url: "https://derpibooru.org/images/3844783" }
    ).window.document;
    expect(extractDerpibooruImage(docWithImage)).toBe(
      "https://derpicdn.net/img/2026/6/28/3844783/full.jpg"
    );
  });

  it("uses site-specific extractor via extractTags", () => {
    expect(extractTags(doc)).toContain("safe");
  });
});

describe("formatTags", () => {
  it("deduplicates and sorts", () => {
    expect(formatTags(["pony", "safe", "solo", "pony", "mlp"])).toBe("mlp, pony, safe, solo");
  });
});

describe("tantabus.ai", () => {
  it("extracts tags from hidden input like derpibooru", () => {
    const doc = new JSDOM(
      '<html><body><input id="tags-form_old_tag_input" value="explicit, creator:eryth, pinkie pie"></body></html>',
      { url: "https://tantabus.ai/images/83727" }
    ).window.document;
    expect(extractTags(doc)).toEqual(["explicit", "creator:eryth", "pinkie pie"]);
  });

  it("upgrades medium image URL to full", () => {
    const doc = new JSDOM(
      '<html><body><picture><img id="image-display" src="https://tantabuscdn.net/img/2026/6/28/83727/medium.jpg" class="image-scaled"></picture></body></html>',
      { url: "https://tantabus.ai/images/83727" }
    ).window.document;
    expect(extractImageUrl(doc)).toBe(
      "https://tantabuscdn.net/img/2026/6/28/83727/full.jpg"
    );
  });
});

describe("reddit.com", () => {
  it("extracts post title and subreddit as tags", () => {
    const doc = new JSDOM(
      `<html><body>
        <shreddit-post post-title="Intense anal [FM] (Bonifasko)" subreddit-prefixed-name="r/FurryPornSubreddit"></shreddit-post>
      </body></html>`,
      { url: "https://www.reddit.com/r/FurryPornSubreddit/comments/abc123/test/" }
    ).window.document;
    expect(extractTags(doc)).toEqual([
      "Intense anal [FM] (Bonifasko)",
      "r/FurryPornSubreddit",
    ]);
  });

  it("prefers largest #post-image srcset entry", () => {
    const doc = new JSDOM(
      `<html><body>
        <img id="post-image"
          src="https://cf.preview.redd.it/intense-anal-fm-bonifasko-v0-j356yhqyav9h1.jpeg?width=640&amp;crop=smart&amp;auto=webp&amp;s=8b7e5c4ae6f59ee3b4527a409f39c32f6043a9ab"
          srcset="https://cf.preview.redd.it/intense-anal-fm-bonifasko-v0-j356yhqyav9h1.jpeg?width=320&amp;crop=smart&amp;auto=webp&amp;s=fc42490674b77dafc0f7c5bfc22cf527b1645914 320w, https://cf.preview.redd.it/intense-anal-fm-bonifasko-v0-j356yhqyav9h1.jpeg?width=640&amp;crop=smart&amp;auto=webp&amp;s=8b7e5c4ae6f59ee3b4527a409f39c32f6043a9ab 640w, https://cf.preview.redd.it/intense-anal-fm-bonifasko-v0-j356yhqyav9h1.jpeg?auto=webp&amp;s=b4ce406912d0c582596224c57fee40700497d76e 980w">
      </body></html>`,
      { url: "https://www.reddit.com/r/FurryPornSubreddit/comments/abc123/test/" }
    ).window.document;
    expect(extractImageUrl(doc)).toBe(
      "https://cf.preview.redd.it/intense-anal-fm-bonifasko-v0-j356yhqyav9h1.jpeg?auto=webp&s=b4ce406912d0c582596224c57fee40700497d76e"
    );
  });

  it("prefers shreddit-post content-href for direct image links", () => {
    const doc = new JSDOM(
      `<html><body>
        <shreddit-post content-href="https://i.redd.it/abc123.jpeg"></shreddit-post>
        <img id="post-image" src="https://cf.preview.redd.it/preview.jpeg?width=640">
      </body></html>`,
      { url: "https://www.reddit.com/r/test/comments/abc123/test/" }
    ).window.document;
    expect(extractImageUrl(doc)).toBe("https://i.redd.it/abc123.jpeg");
  });
});
