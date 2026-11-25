// Builds the design-review prototypes into ONE self-contained HTML file.
//   cd frontend
//   node design-review/build-standalone.mjs
// Output: design-review/UC3_Design_Review.html (open by double-click; no server needed).
import { execSync } from "node:child_process";
import { mkdtempSync, readFileSync, readdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const out = mkdtempSync(join(tmpdir(), "uc3-dr-"));
try {
  execSync(`npx vite build --config "${join(here, "vite.config.mjs")}" --outDir "${out}" --emptyOutDir`, {
    stdio: "inherit",
    cwd: join(here, ".."),
  });
  let html = readFileSync(join(out, "index.html"), "utf8");
  const assets = readdirSync(join(out, "assets"));
  for (const f of assets) {
    const body = readFileSync(join(out, "assets", f), "utf8");
    if (f.endsWith(".js")) {
      const tag = new RegExp(`<script[^>]*src="\\./assets/${f.replace(/\./g, "\\.")}"[^>]*></script>`);
      // A literal "</script" inside the bundle would end the inline tag early.
      const safe = body.replace(/<\/script/gi, "<\\/script");
      html = html.replace(tag, () => `<script type="module">\n${safe}\n</script>`);
    } else if (f.endsWith(".css")) {
      const tag = new RegExp(`<link[^>]*href="\\./assets/${f.replace(/\./g, "\\.")}"[^>]*>`);
      html = html.replace(tag, () => `<style>\n${body}\n</style>`);
    }
  }
  if (/\.\/assets\//.test(html)) throw new Error("an asset reference was not inlined");
  const dest = join(here, "UC3_Design_Review.html");
  writeFileSync(dest, html, "utf8");
  console.log(`written ${dest} (${Math.round(html.length / 1024)} KB)`);
} finally {
  rmSync(out, { recursive: true, force: true });
}
