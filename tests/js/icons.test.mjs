// The canvas half of the icon rendering (run through test_roundrep_js.py,
// argv[2] = the fixture icon folder with manifest.json):
//
// The silhouettes are TINTED through the alpha mask, never through the
// file's own fill: an icon is drawn into a throwaway canvas, then
// `source-in` with a colour keeps exactly the painted pixels and recolours
// them. Part of the real art (grenades, the bomb, the defuse kit) carries
// no `fill="white"` and would stay BLACK without this - the skull fixture
// (no fill attribute at all) stands in for exactly that art.
import fs from "fs";
import path from "path";
import assert from "assert";
import { createCanvas, loadImage } from "@napi-rs/canvas";

const dir = process.argv[2];
const manifest = JSON.parse(fs.readFileSync(path.join(dir, "manifest.json"), "utf8"));
const ok = (msg) => console.log("  ok  " + msg);

const uri = (key) =>
  "data:image/svg+xml;base64," + fs.readFileSync(path.join(dir, manifest[key].file)).toString("base64");

const near = (px, want, tol = 2) =>
  Math.abs(px[0] - want[0]) <= tol && Math.abs(px[1] - want[1]) <= tol &&
  Math.abs(px[2] - want[2]) <= tol && Math.abs(px[3] - want[3]) <= tol;

// the tint roundrep.js does in tintedIcon(): draw, source-in a colour
async function tinted(key, color, w, h) {
  const im = await loadImage(uri(key));
  const cv = createCanvas(w, h);
  const ctx = cv.getContext("2d");
  ctx.drawImage(im, 0, 0, w, h);
  ctx.globalCompositeOperation = "source-in";
  ctx.fillStyle = color;
  ctx.fillRect(0, 0, w, h);
  ctx.globalCompositeOperation = "source-over";
  return cv;
}

// 1) a fill-less silhouette (the grenade-style art) tints to the colour
const TINT = [52, 152, 219, 255]; // #3498db
const sk = await tinted("skull", "#3498db", 24, 24);
const skCtx = sk.getContext("2d");
assert.ok(near(skCtx.getImageData(12, 12, 1, 1).data, TINT),
  "center pixel after tint: " + skCtx.getImageData(12, 12, 1, 1).data);
assert.strictEqual(skCtx.getImageData(0, 0, 1, 1).data[3], 0); // outside: transparent
ok("a fill-less silhouette tints through source-in (centre #3498db, outside alpha 0)");

// 2) the same file WITHOUT the tint stays black - the reason the mask exists
const skRaw = await loadImage(uri("skull"));
const cvRaw = createCanvas(24, 24);
const rawCtx = cvRaw.getContext("2d");
rawCtx.drawImage(skRaw, 0, 0, 24, 24);
const rawPx = rawCtx.getImageData(12, 12, 1, 1).data;
assert.ok(near(rawPx, [0, 0, 0, 255], 40), "raw draw pixel: " + rawPx);
ok("without the tint the same file draws black - the mask is not optional");

// 3) a white-filled weapon silhouette tints to the same colour (any art works)
const ak = await tinted("ak47", "#3498db", 100, 32);
const akCtx = ak.getContext("2d");
assert.ok(near(akCtx.getImageData(50, 16, 1, 1).data, TINT),
  "ak centre after tint: " + akCtx.getImageData(50, 16, 1, 1).data);
ok("a white-filled weapon silhouette tints the same way");

// 4) two colours of the same icon never bleed into each other (cache keys)
const a = await tinted("headshot", "#ff0000", 24, 24);
const b = await tinted("headshot", "#00ff00", 24, 24);
const aPx = a.getContext("2d").getImageData(12, 12, 1, 1).data;
const bPx = b.getContext("2d").getImageData(12, 12, 1, 1).data;
assert.ok(near(aPx, [255, 0, 0, 255]) && near(bPx, [0, 255, 0, 255]),
  "red " + aPx + " vs green " + bPx);
ok("each colour composites independently (no cross-bleed)");

console.log("ICONS OK");
