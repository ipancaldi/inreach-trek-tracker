// Shared helpers for the Garmin LiveTrack / MapShare proxy functions.

const UA = "Mozilla/5.0 (Macintosh) inReach-Trek-Tracker/1.0";

/** Fetch a live.garmin.com / livetrack.garmin.com page as a Next.js RSC
 *  flight stream — the JSON session data appears unescaped in the body. */
export async function fetchRsc(url: string): Promise<string> {
  const r = await fetch(url, { headers: { "User-Agent": UA, RSC: "1" } });
  if (!r.ok) throw new Error(`Garmin returned HTTP ${r.status}`);
  return await r.text();
}

/** Brace/bracket-match a JSON value starting at text[start], string-aware. */
export function extractJsonValue(text: string, start: number): any {
  const openCh = text[start];
  const closeCh = openCh === "{" ? "}" : "]";
  let depth = 0;
  let inStr = false;
  let esc = false;
  for (let i = start; i < text.length; i++) {
    const c = text[i];
    if (inStr) {
      if (esc) esc = false;
      else if (c === "\\") esc = true;
      else if (c === '"') inStr = false;
    } else if (c === '"') {
      inStr = true;
    } else if (c === openCh) {
      depth++;
    } else if (c === closeCh) {
      depth--;
      if (depth === 0) return JSON.parse(text.slice(start, i + 1));
    }
  }
  throw new Error("Unbalanced JSON");
}

/** Sessions via Garmin's REST API. The profile page only embeds session
 *  lists sometimes (never while a walk is live) — the reliable source is
 *  /api/user/{guid}/profile-sessions, authenticated with the CSRF token
 *  and cookies handed out by the profile page itself. */
export async function livetrackSessionsViaApi(name: string): Promise<any | null> {
  const r = await fetch("https://live.garmin.com/" + encodeURIComponent(name), {
    headers: { "User-Agent": UA },
  });
  if (!r.ok) return null;
  const html = await r.text();
  const tok = html.match(/name="csrf-token" content="([^"]+)"/)?.[1];
  const guid = html.match(/garminGuid\\?":\\?"([0-9a-f-]{36})/)?.[1];
  if (!tok || !guid) return null;
  const cookies = (r.headers.getSetCookie?.() ?? [])
    .map((c: string) => c.split(";")[0])
    .join("; ");
  const r2 = await fetch(
    `https://live.garmin.com/api/user/${guid}/profile-sessions?limit=20`,
    {
      headers: {
        "User-Agent": UA,
        "Livetrack-Csrf-Token": tok,
        Accept: "application/json",
        ...(cookies ? { Cookie: cookies } : {}),
      },
    },
  );
  if (!r2.ok) return null;
  return await r2.json();
}

/** JSON response with CORS open — the GitHub Pages copy of the app calls
 *  these functions cross-origin. The data is Garmin's public share feed. */
export function corsJson(obj: unknown, status = 200): Response {
  return new Response(JSON.stringify(obj), {
    status,
    headers: {
      "Content-Type": "application/json",
      "Access-Control-Allow-Origin": "*",
      "Cache-Control": "no-store",
    },
  });
}
