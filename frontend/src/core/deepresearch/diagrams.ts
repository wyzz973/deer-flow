/**
 * Pictures for the Word export.
 *
 * Mermaid needs a browser to draw, and this product has to run on an intranet
 * with no rendering service and no outbound network, so the page that already
 * shows the diagrams draws them for the export too. Each picture is filed
 * under its own source text, which is what the server matches against the
 * document it holds: a picture can only ever land under the diagram it came
 * from, and a diagram that fails to draw simply keeps its caption.
 */

import { mermaid } from "@streamdown/mermaid";

import { mermaidSources, normalizeMermaidMarkdown } from "@/core/streamdown";

// Word prints at roughly twice screen density; a 1x raster looks soft on paper.
const SCALE = 2;
const MAX_EDGE = 4000;
const MAX_DIAGRAMS = 60;

/**
 * Mermaid lays labels out with `<foreignObject>` by default, and a browser
 * refuses to export a canvas that such an SVG was drawn onto ("tainted
 * canvas"), so every diagram would fail to rasterise. Plain SVG text carries
 * the same diagram and can be exported. Labels wrap slightly differently from
 * what the page shows; that is the cost of having a picture at all.
 */
const FOR_EXPORT = {
  htmlLabels: false,
  flowchart: { htmlLabels: false },
} as const;
/** What the page draws with, restored once the export has its pictures. */
const FOR_READING = {
  htmlLabels: true,
  flowchart: { htmlLabels: true },
} as const;

/** The identity the server files a picture under. Mirrors `diagram_key` in report.py. */
function key(source: string): string {
  return source.trim().split(/\s+/).join(" ");
}

/**
 * The source as the renderer wants it.
 *
 * The page normalizes whole documents before drawing them, so one block is
 * wrapped back into a fence and put through the same function rather than
 * having a second copy of those rules here.
 */
function drawable(source: string): string {
  return (
    mermaidSources(
      normalizeMermaidMarkdown("```mermaid\n" + source + "\n```"),
    )[0] ?? source
  );
}

/**
 * Give the root `<svg>` a concrete size.
 *
 * Mermaid sizes its output with a viewBox and a percentage width. An `<img>`
 * then reports a natural size of zero in several browsers, and the diagram
 * would be drawn onto a zero-sized canvas.
 */
function sized(svg: string): string {
  const root = new DOMParser().parseFromString(
    svg,
    "image/svg+xml",
  ).documentElement;
  const box = root
    .getAttribute("viewBox")
    ?.split(/[\s,]+/)
    .map(Number);

  if (box?.length === 4 && box[2]! > 0 && box[3]! > 0) {
    root.setAttribute("width", String(box[2]));
    root.setAttribute("height", String(box[3]));
  }

  return new XMLSerializer().serializeToString(root);
}

async function png(svg: string): Promise<string | undefined> {
  const url = URL.createObjectURL(
    new Blob([svg], { type: "image/svg+xml;charset=utf-8" }),
  );

  try {
    const image = new Image();
    await new Promise<void>((resolve, reject) => {
      image.onload = () => resolve();
      image.onerror = () => reject(new Error("diagram did not load"));
      image.src = url;
    });

    const width = image.naturalWidth || image.width;
    const height = image.naturalHeight || image.height;

    if (!width || !height) {
      return undefined;
    }

    const scale = Math.min(SCALE, MAX_EDGE / Math.max(width, height));
    const canvas = document.createElement("canvas");
    canvas.width = Math.round(width * scale);
    canvas.height = Math.round(height * scale);

    const context = canvas.getContext("2d");
    if (!context) {
      return undefined;
    }

    // A Word page has no background of its own, and Mermaid draws its labels
    // in near-black on transparency; without this the text prints on grey.
    context.fillStyle = "#ffffff";
    context.fillRect(0, 0, canvas.width, canvas.height);
    context.drawImage(image, 0, 0, canvas.width, canvas.height);

    const encoded = canvas.toDataURL("image/png");
    return encoded.slice(encoded.indexOf(",") + 1);
  } finally {
    URL.revokeObjectURL(url);
  }
}

/**
 * Draw this document's Mermaid blocks and return them keyed by source.
 *
 * Never throws: one diagram that will not draw must not cost the reader the
 * whole export, and the server keeps a caption and the source for whatever is
 * missing.
 */
export async function renderDiagrams(
  markdown: string,
): Promise<Record<string, string>> {
  const sources = mermaidSources(markdown).slice(0, MAX_DIAGRAMS);
  const diagrams: Record<string, string> = {};

  if (sources.length === 0) {
    return diagrams;
  }

  const instance = mermaid.getMermaid(FOR_EXPORT);

  try {
    for (const [index, source] of sources.entries()) {
      try {
        const { svg } = await instance.render(
          `deepresearch-export-${index}`,
          drawable(source),
        );
        const data = await png(sized(svg));

        if (data) {
          diagrams[key(source)] = data;
        }
      } catch (error) {
        // One diagram that will not draw keeps its caption and its source in
        // an appendix. Say why, or a missing picture has no explanation.
        console.warn("DeepResearch: 这张图无法导出为图片", error);
      }
    }
  } finally {
    instance.initialize(FOR_READING);
  }

  return diagrams;
}
