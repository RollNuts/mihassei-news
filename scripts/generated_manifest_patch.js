/* Append one image or review record without reserializing existing JSON numbers.
 * Pure V8 / code-mode: new Function(source + '\nreturn GeneratedManifestPatch;')().
 * This only edits text; it does not save, verify provenance, approve or publish.
 */
var GeneratedManifestPatch = (function () {
  "use strict";
  const SLUG = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;
  const object = value => value !== null && typeof value === "object" && !Array.isArray(value);

  function inspect(source, label) {
    if (typeof source !== "string" || !source.trim()) throw Error("Invalid " + label + " JSON source");
    let parsed;
    try { parsed = JSON.parse(source); }
    catch (_) { throw Error("Invalid " + label + " JSON source"); }
    // JSON.parse validates syntax only. These offsets refer to the untouched
    // source, including number literals outside JavaScript's safe integer range.
    let at = 0;
    function space() { while (/[ \t\r\n]/.test(source[at] || "x")) at++; }
    function string() {
      const start = at++;
      while (at < source.length) {
        if (source[at] === "\\") at += 2;
        else if (source[at++] === '"') return JSON.parse(source.slice(start, at));
      }
      throw Error("Unterminated JSON string");
    }
    function value(depth) {
      if (depth > 128) throw Error("JSON nesting is too deep");
      space();
      const start = at, c = source[at], members = [];
      if (c === "{") {
        at++; space();
        const keys = new Set();
        if (source[at] !== "}") while (true) {
          const key = string();
          if (keys.has(key)) throw Error("Duplicate JSON member in " + label + ": " + key);
          keys.add(key); space(); at++; // colon: syntax already validated
          members.push({ key, node: value(depth + 1) });
          space();
          if (source[at] !== ",") break;
          at++; space();
        }
        at++;
        return { type: "object", start, end: at, members };
      }
      if (c === "[") {
        at++; space();
        if (source[at] !== "]") while (true) {
          value(depth + 1); space();
          if (source[at] !== ",") break;
          at++;
        }
        at++;
      } else if (c === '"') string();
      else while (at < source.length && !/[ \t\r\n,\]}]/.test(source[at])) at++;
      return { type: c === "[" ? "array" : "scalar", start, end: at };
    }
    const node = value(0);
    space();
    if (at !== source.length) throw Error("Invalid " + label + " JSON boundary");
    return { parsed, node };
  }

  function appendImage({ manifestSource, slug, imageSource } = {}) {
    if (typeof slug !== "string" || !SLUG.test(slug)) throw Error("Invalid article slug");
    const manifest = inspect(manifestSource, "manifest"), image = inspect(imageSource, "image");
    if (!object(manifest.parsed) || manifest.parsed.version !== 1
        || !object(manifest.parsed.images) || !object(manifest.parsed.rejected))
      throw Error("Expected a version-1 generated image manifest");
    if (!object(image.parsed)) throw Error("Image record must be a JSON object");
    const images = manifest.node.members.find(member => member.key === "images").node;
    if (images.members.some(member => member.key === slug))
      throw Error("Image record already exists; append cannot replace it");
    const insertion = (images.members.length ? "," : "")
      + "\n    " + JSON.stringify(slug) + ": " + imageSource + "\n  ";
    const close = images.end - 1;
    return manifestSource.slice(0, close) + insertion + manifestSource.slice(close);
  }
  function appendReview({ reviewsSource, slug, reviewSource } = {}) {
    if (typeof slug !== "string" || !SLUG.test(slug)) throw Error("Invalid article slug");
    const reviews = inspect(reviewsSource, "reviews"), review = inspect(reviewSource, "review");
    if (!object(reviews.parsed) || reviews.parsed.version !== 1 || !object(reviews.parsed.reviews))
      throw Error("Expected a version-1 visual review document");
    if (!object(review.parsed)) throw Error("Review record must be a JSON object");
    const entries = reviews.node.members.find(member => member.key === "reviews").node;
    if (entries.members.some(member => member.key === slug))
      throw Error("Review record already exists; append cannot replace it");
    const insertion = (entries.members.length ? "," : "")
      + "\n    " + JSON.stringify(slug) + ": " + reviewSource + "\n  ";
    const close = entries.end - 1;
    return reviewsSource.slice(0, close) + insertion + reviewsSource.slice(close);
  }
  return Object.freeze({ appendImage, appendReview });
})();
if (typeof module !== "undefined" && module.exports) module.exports = GeneratedManifestPatch;
