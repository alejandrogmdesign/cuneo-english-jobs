// Stores the websites she adds to, or removes from, the scan list.
//
//   GET  /sources   -> { added: [{id, url, name, addedAt}], removed: [id, ...] }   (public: the page and the scanner read it)
//   POST /sources   -> { op: "add", url, name? } | { op: "remove", id } | { op: "restore", id }
//                      needs  Authorization: Bearer <EDIT_KEY>  (the key in her private link)
//
// Bindings (wrangler.toml / dashboard):
//   SOURCES         KV namespace
//   EDIT_KEY        secret: the key in her private link
//   ALLOWED_ORIGIN  the page's origin, e.g. https://alejandrogmdesign.github.io
//   GH_REPO, GH_TOKEN  optional: "owner/repo" and a fine-grained token (Actions: write) to start a scan right after she adds a site

const MAX_ADDED = 300;

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    const cors = {
      "Access-Control-Allow-Origin": env.ALLOWED_ORIGIN || "*",
      "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
      "Access-Control-Allow-Headers": "Content-Type, Authorization",
      "Access-Control-Max-Age": "86400",
      Vary: "Origin",
    };
    const reply = (body, status = 200) =>
      new Response(JSON.stringify(body), { status, headers: { ...cors, "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store" } });

    if (request.method === "OPTIONS") return new Response(null, { status: 204, headers: cors });
    if (url.pathname === "/fetch") return relay(request, url, env);
    if (url.pathname !== "/sources") return reply({ error: "Not found." }, 404);

    const data = (await env.SOURCES.get("list", { type: "json" })) || { added: [], removed: [] };
    if (request.method === "GET") return reply(data);
    if (request.method !== "POST") return reply({ error: "Use GET or POST." }, 405);

    if (!(await keyMatches(request.headers.get("Authorization") || "", env.EDIT_KEY))) {
      return reply({ error: "This link can’t change the list. Open the private link Alex sent you." }, 401);
    }
    let body;
    try {
      body = await request.json();
    } catch {
      return reply({ error: "The request wasn’t readable." }, 400);
    }

    if (body.op === "add") {
      const site = normaliseUrl(body.url);
      if (!site) return reply({ error: "That doesn’t look like a web address. Paste one starting with https://" }, 400);
      if (data.added.length >= MAX_ADDED) return reply({ error: "The list is full. Remove a website first." }, 400);
      const id = "web-" + (await sha1(site)).slice(0, 10);
      if (!data.added.some((a) => a.id === id)) {
        data.added.push({ id, url: site, name: String(body.name || "").trim().slice(0, 80), addedAt: new Date().toISOString() });
      }
      data.removed = data.removed.filter((r) => r !== id);
      await env.SOURCES.put("list", JSON.stringify(data));
      if (env.GH_REPO && env.GH_TOKEN) ctx.waitUntil(startScan(env));
      return reply(data);
    }

    const id = String(body.id || "").slice(0, 40);
    if (!/^[\w-]+$/.test(id)) return reply({ error: "Unknown website." }, 400);
    if (body.op === "remove") {
      const before = data.added.length;
      data.added = data.added.filter((a) => a.id !== id); // a site she added is deleted outright
      if (data.added.length === before && !data.removed.includes(id)) data.removed.push(id);
    } else if (body.op === "restore") {
      data.removed = data.removed.filter((r) => r !== id);
    } else {
      return reply({ error: "Unknown change." }, 400);
    }
    await env.SOURCES.put("list", JSON.stringify(data));
    return reply(data);
  },
};

// Some school servers refuse GitHub's runners (rate limits per IP, bot checks for datacenter
// addresses). The scanner retries those through here. Only the scanner has SCAN_KEY.
async function relay(request, url, env) {
  if (request.method !== "GET") return new Response("GET only", { status: 405 });
  if (!(await keyMatches(request.headers.get("X-Scan-Key") || "", env.SCAN_KEY, ""))) return new Response("Forbidden", { status: 403 });
  let target;
  try {
    target = new URL(url.searchParams.get("url") || "");
  } catch {
    return new Response("Bad url", { status: 400 });
  }
  if (!/^https?:$/.test(target.protocol) || /(^|\.)workers\.dev$/i.test(target.hostname)) return new Response("Bad url", { status: 400 });
  try {
    const r = await fetch(target.toString(), {
      redirect: "follow",
      headers: {
        "User-Agent": request.headers.get("User-Agent") || "Mozilla/5.0",
        "Accept-Language": "it-IT,it;q=0.9,en;q=0.5",
      },
    });
    return new Response(r.body, {
      status: 200,
      headers: {
        "Content-Type": r.headers.get("Content-Type") || "application/octet-stream",
        "X-Upstream-Status": String(r.status),
        "X-Upstream-Url": r.url || target.toString(),
        "Cache-Control": "no-store",
      },
    });
  } catch (e) {
    return new Response(String(e).slice(0, 200), { status: 502, headers: { "X-Upstream-Status": "0" } });
  }
}

function normaliseUrl(raw) {
  try {
    let s = String(raw || "").trim();
    if (!/^https?:\/\//i.test(s)) s = "https://" + s;
    const u = new URL(s);
    if (!/^https?:$/.test(u.protocol) || !u.hostname.includes(".") || s.length > 400) return null;
    u.hash = "";
    return u.toString().replace(/\/$/, "");
  } catch {
    return null;
  }
}

async function sha1(text) {
  const buf = await crypto.subtle.digest("SHA-1", new TextEncoder().encode(text));
  return [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

async function keyMatches(header, key, prefix = "Bearer ") {
  if (!key) return false;
  const enc = new TextEncoder();
  const a = enc.encode(header);
  const b = enc.encode(prefix + key);
  if (a.byteLength !== b.byteLength) return false;
  return crypto.subtle.timingSafeEqual(a, b);
}

async function startScan(env) {
  await fetch(`https://api.github.com/repos/${env.GH_REPO}/actions/workflows/scan.yml/dispatches`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${env.GH_TOKEN}`,
      Accept: "application/vnd.github+json",
      "User-Agent": "cuneo-english-jobs-worker",
      "X-GitHub-Api-Version": "2022-11-28",
    },
    body: JSON.stringify({ ref: "main" }),
  }).catch(() => {});
}
