// Mirrors stitch_cli/tuning.py — emits the same inkstitch:* attributes
// straight at SVG-generation time so we can skip the tuning pass.
//
// Defaults baked in for the "hat-twill-yellow-on-black" preset:
//   row_spacing_mm      = 0.40
//   max_stitch_length_mm = 3.00
//   pull_compensation_mm = 0.20  (twill-hat-front fabric)
// These match materials/presets.toml + materials/fabrics.toml at the time
// of writing. Override via the `tuning` argument if a different preset is
// being targeted.

export const INKSTITCH_NS = "http://inkstitch.org/namespace";
export const SVG_NS = "http://www.w3.org/2000/svg";
export const INKSTITCH_SVG_VERSION = "3";

export const DEFAULT_TUNING = {
  row_spacing_mm: 0.4,
  max_stitch_length_mm: 3.0,
  pull_compensation_mm: 0.2,
  fill_underlay_inset_mm: 1.0,
};

const fmt2 = (n) => Number(n).toFixed(2);

// Build a <metadata><inkstitch:inkstitch_svg_version>3</…></…> child for the
// SVG root, matching stitch_cli/tuning.py:_stamp_svg_version.
export function metadataXml() {
  return (
    `<metadata>` +
    `<inkstitch:inkstitch_svg_version>${INKSTITCH_SVG_VERSION}</inkstitch:inkstitch_svg_version>` +
    `</metadata>`
  );
}

// Returns a string of attribute pairs ready to splice into a <path …/>.
// kind: "fill" | "satin" | "running"
export function inkstitchAttrs(kind, tuning = DEFAULT_TUNING) {
  const t = { ...DEFAULT_TUNING, ...tuning };
  const row = fmt2(t.row_spacing_mm);
  const maxLen = fmt2(t.max_stitch_length_mm);
  const pull = fmt2(t.pull_compensation_mm);
  const inset = fmt2(t.fill_underlay_inset_mm);

  if (kind === "fill") {
    return [
      `inkstitch:fill_method="auto_fill"`,
      `inkstitch:row_spacing_mm="${row}"`,
      `inkstitch:max_stitch_length_mm="${maxLen}"`,
      `inkstitch:fill_underlay="true"`,
      `inkstitch:fill_underlay_inset_mm="${inset}"`,
      `inkstitch:expand_mm="${pull}"`,
    ].join(" ");
  }
  if (kind === "satin") {
    return [
      `inkstitch:satin_column="true"`,
      `inkstitch:max_stitch_length_mm="${maxLen}"`,
      `inkstitch:center_walk_underlay="true"`,
      `inkstitch:zigzag_underlay="true"`,
      `inkstitch:pull_compensation_mm="${pull}"`,
    ].join(" ");
  }
  if (kind === "running") {
    return [
      `inkstitch:stroke_method="running_stitch"`,
      `inkstitch:running_stitch_length_mm="${maxLen}"`,
    ].join(" ");
  }
  return "";
}
