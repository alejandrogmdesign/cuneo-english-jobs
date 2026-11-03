// Headless check of docs/index.html: screenshots at phone and desktop widths, light and dark,
// and the add/remove flow against a mocked sources API.
//   node scripts/check-page.mjs <docs dir> <output dir>
import { createRequire } from "module";
import http from "http";
import fs from "fs";
import path from "path";

const require = createRequire("/Users/agomezmeade/Claude/Projects/Business/AGM Website Redesign/tools/headless/package.json");
const { chromium } = require("playwright-core");

const root = path.resolve(process.argv[2]);
const out = path.resolve(process.argv[3]);
fs.mkdirSync(out, { recursive: true });
const types = { ".html": "text/html; charset=utf-8", ".js": "text/javascript", ".json": "application/json" };
const server = http.createServer((req, res) => {
  const p = path.join(root, decodeURIComponent(new URL(req.url, "http://x").pathname).replace(/\/$/, "/index.html"));
  if (!p.startsWith(root) || !fs.existsSync(p)) { res.writeHead(404); return res.end(); }
  res.writeHead(200, { "Content-Type": types[path.extname(p)] || "application/octet-stream" });
  fs.createReadStream(p).pipe(res);
}).listen(0, "127.0.0.1");
await new Promise((r) => server.once("listening", r));
const base = `http://127.0.0.1:${server.address().port}/`;

const browser = await chromium.launch({ channel: "chrome", headless: true });
const report = [];

async function open({ width, height, scheme, key }) {
  const ctx = await browser.newContext({ viewport: { width, height }, colorScheme: scheme, deviceScaleFactor: 2 });
  const page = await ctx.newPage();
  const errors = [];
  page.on("console", (m) => m.type() === "error" && errors.push(m.text()));
  page.on("pageerror", (e) => errors.push(e.message));
  const live = { added: [], removed: [] };
  await page.route("**/config.js", (r) => r.fulfill({ contentType: "text/javascript", body: 'window.CUNEO_JOBS_CONFIG={sourcesApi:"https://api.test"};' }));
  await page.route("https://api.test/**", async (r) => {
    const req = r.request();
    const cors = { "Access-Control-Allow-Origin": "*", "Access-Control-Allow-Headers": "Content-Type, Authorization", "Access-Control-Allow-Methods": "GET, POST, OPTIONS" };
    if (req.method() === "OPTIONS") return r.fulfill({ status: 204, headers: cors });
    if (req.method() === "POST") {
      if (req.headers()["authorization"] !== "Bearer testkey") return r.fulfill({ status: 401, headers: cors, contentType: "application/json", body: '{"error":"bad key"}' });
      const b = req.postDataJSON();
      if (b.op === "add") live.added.push({ id: "web-test1", url: b.url, name: b.name, addedAt: new Date().toISOString() });
      if (b.op === "remove") { const n = live.added.length; live.added = live.added.filter((a) => a.id !== b.id); if (n === live.added.length) live.removed.push(b.id); }
      if (b.op === "restore") live.removed = live.removed.filter((x) => x !== b.id);
    }
    return r.fulfill({ headers: cors, contentType: "application/json", body: JSON.stringify(live) });
  });
  await page.goto(base + (key ? "#key=testkey" : ""), { waitUntil: "networkidle" });
  await page.waitForSelector("#notices li");
  return { ctx, page, errors };
}

for (const v of [
  { name: "phone-light", width: 390, height: 844, scheme: "light" },
  { name: "phone-dark", width: 390, height: 844, scheme: "dark" },
  { name: "desktop-light", width: 1280, height: 900, scheme: "light" },
]) {
  const { ctx, page, errors } = await open(v);
  await page.screenshot({ path: path.join(out, `${v.name}.png`), fullPage: true });
  const m = await page.evaluate(() => ({
    overflowX: document.documentElement.scrollWidth > innerWidth,
    open: document.querySelector("#count-open").textContent,
    past: document.querySelector("#count-past").textContent,
    status: document.querySelector("#status").textContent,
    font: getComputedStyle(document.body).fontFamily.split(",")[0],
    fontLoaded: document.fonts.check('16px "Atkinson Hyperlegible Next"'),
  }));
  report.push({ view: v.name, ...m, errors });
  await ctx.close();
}

// Edit flow on a phone with the private key.
{
  const { ctx, page, errors } = await open({ width: 390, height: 844, scheme: "light", key: true });
  const hashGone = await page.evaluate(() => location.hash === "");
  await page.click("#open-sources");
  await page.waitForSelector("#sources[open]");
  await page.screenshot({ path: path.join(out, "sources-dialog.png") });
  await page.fill("#add-url", "britishschool-cuneo.example.it/lavora-con-noi");
  await page.fill("#add-name", "British School Cuneo");
  await page.click("#addform button[type=submit]");
  await page.waitForFunction(() => /Added/.test(document.querySelector("#add-msg").textContent));
  const firstRemove = await page.$("#source-list button[data-remove]");
  await firstRemove.click();
  await page.waitForTimeout(300);
  const g = await page.evaluate(() => [...document.querySelectorAll(".src-group h3")].map((h) => h.textContent));
  await page.screenshot({ path: path.join(out, "sources-after-edit.png") });
  // reload: key remembered, removed list persists via the API mock in this context
  report.push({ view: "edit-flow", hashGone, groups: g, addMsg: await page.textContent("#add-msg"), errors });
  await page.keyboard.press("Escape");
  await ctx.close();
}
// Read-only view (no key): no add form.
{
  const { ctx, page, errors } = await open({ width: 390, height: 844, scheme: "light" });
  await page.click("#open-sources");
  report.push({ view: "read-only", addFormHidden: await page.$eval("#addform", (f) => f.hidden), removeButtons: (await page.$$("#source-list button")).length, errors });
  await ctx.close();
}

console.log(JSON.stringify(report, null, 1));
await browser.close();
server.close();
