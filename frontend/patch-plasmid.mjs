// Fixes to the vendored plasmid editor (Open Vector Editor), applied by
// `npm run build:plasmid` after it copies the package into app/static.
// Each must apply exactly as many times as expected, so a new version of the
// package that changes them stops the build rather than shipping the old bug.
import { readFileSync, writeFileSync } from 'node:fs';

const file = '../app/static/notebook-build/plasmid-app.js';
let text = readFileSync(file, 'utf8');

const fixes = [
  // The Mac app's window is WebKit, like Safari, but its user agent never says
  // "Safari": the editor then sized the sequence's letters the Chrome way and
  // drew them too narrow for the ruler and the features above them (#57).
  // Any WebKit that isn't Chrome or Android is treated as Safari: here, in its
  // Browser helper, and in the pop-up positioning (Safari's viewport rules).
  ['/^((?!chrome|android).)*safari/i', '/^(?!.*(chrome|android)).*(safari|applewebkit)/i', 3],
  // Export showed the "import" arrow and Import the "export" one (#57).
  ['{ "data-test": "veDownloadTool", icon: "import" }', '{ "data-test": "veDownloadTool", icon: "export" }', 1],
  ['{ "data-test": "veImportTool", icon: "export" }', '{ "data-test": "veImportTool", icon: "import" }', 1],
];

for (const [from, to, times] of fixes) {
  const found = text.split(from).length - 1;
  if (found !== times) {
    console.error(`patch-plasmid: expected ${times} of ${from}, found ${found}`);
    process.exit(1);
  }
  text = text.split(from).join(to);
}
writeFileSync(file, text);
console.log(`patch-plasmid: ${fixes.length} fixes applied`);
