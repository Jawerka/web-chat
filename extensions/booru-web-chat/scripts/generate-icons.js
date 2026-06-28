import { readFileSync, mkdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import sharp from "sharp";

const __dirname = dirname(fileURLToPath(import.meta.url));
const rootDir = join(__dirname, "..");
const iconsDir = join(rootDir, "icons");
const svgPath = join(iconsDir, "icon.svg");

mkdirSync(iconsDir, { recursive: true });

const svg = readFileSync(svgPath);
const sizes = [16, 48, 128];

await Promise.all(
  sizes.map(async (size) => {
    const outPath = join(iconsDir, `icon${size}.png`);
    await sharp(svg, { density: Math.max(72, Math.round((size / 128) * 512)) })
      .resize(size, size, { fit: "contain", background: { r: 0, g: 0, b: 0, alpha: 0 } })
      .png({ compressionLevel: 9, palette: size <= 16 })
      .toFile(outPath);
  })
);

console.log("Icons generated");
