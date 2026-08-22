// OHHI generative pattern — placeholder PEmbroider sketch.
//
// Reads materials params from environment variables set by `stitch from-generative`:
//   STITCH_PRIMARY_COLOR_HEX  — e.g. "#FFF859"
//   STITCH_ROW_SPACING_MM     — fill density (mm between rows)
//   STITCH_MAX_STITCH_LENGTH_MM
//   STITCH_PULL_COMP_MM
//
// Install:
//   1. Install Processing 4: brew install --cask processing
//   2. Drop PEmbroider into ~/Documents/Processing/libraries/PEmbroider/ from
//      https://github.com/CreativeInquiry/PEmbroider/releases
//   3. Run via the CLI: stitch from-generative generative/ohhi-pattern --preset hat-twill-yellow-on-black

import processing.embroider.*;

PEmbroiderGraphics E;

void setup() {
  size(400, 400);   // 100mm x 100mm at 4px/mm
  E = new PEmbroiderGraphics(this, width, height);

  String hex = System.getenv("STITCH_PRIMARY_COLOR_HEX");
  if (hex == null) hex = "#FFF859";
  int c = unhex(hex.replace("#", "FF"));   // ARGB

  E.setPath(sketchPath("ohhi-pattern.pes"));
  E.setStitch(2.0, 3.0, 0);
  E.beginDraw();
  E.noStroke();
  E.fill(c);
  E.hatchSpacing(parseFloatEnv("STITCH_ROW_SPACING_MM", 0.4) * 4);  // mm → px

  // Placeholder geometry: concentric "OH HI" wave grid.
  for (int i = 0; i < 5; i++) {
    E.circle(width / 2, height / 2, 60 + i * 30);
  }

  E.optimize();
  E.endDraw();
  E.visualize();
  exit();
}

float parseFloatEnv(String key, float fallback) {
  String v = System.getenv(key);
  if (v == null) return fallback;
  try { return Float.parseFloat(v); } catch (Exception e) { return fallback; }
}
