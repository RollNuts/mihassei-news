/* Node CI entry point. Only pinned public dependency sources are fetched;
 * the regression suites use synthetic images and in-memory GitHub fakes.
 */
"use strict";

const bytes = require("./native_image_bytes.js");
const stage = require("./native_image_stage.js");
const testBytes = require("./native_image_bytes_test.js");
const testStage = require("./native_image_stage_test.js");

async function main() {
  const entries = Object.entries(bytes.SOURCES);
  const fetched = await Promise.allSettled(entries.map(async ([key, source]) => {
    const url = `https://raw.githubusercontent.com/${source.repository_full_name}/${source.ref}/${source.path}`;
    const response = await fetch(url, {signal: AbortSignal.timeout(10_000)});
    if (!response.ok) throw Error(`${key}: HTTP ${response.status}`);
    return response.text();
  }));
  const failures = fetched.flatMap((result, index) => result.status === "rejected"
    ? [`${entries[index][0]}: ${result.reason.message || result.reason}`] : []);
  if (failures.length) {
    throw Error("Pinned dependency download failed; native image tests NOT RUN: " + failures.join("; "));
  }
  const sources = Object.fromEntries(entries.map(([key], index) => [key, fetched[index].value]));
  let runtime;
  try {
    runtime = bytes.initialize(sources);
  } catch (error) {
    throw Error("Pinned dependency verification failed; native image tests NOT RUN: " + error.message);
  }
  const byteReport = testBytes(bytes, sources);
  console.log(`native_image_bytes_test.js: ${byteReport.passed} checks passed`);
  const stageReport = await testStage(stage, runtime);
  console.log(`native_image_stage_test.js: ${stageReport.passed} checks passed`);
}

main().catch(error => {
  console.error(error.stack || error);
  process.exitCode = 1;
});
