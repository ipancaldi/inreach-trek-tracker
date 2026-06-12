import { extractJsonValue, fetchRsc, corsJson } from "../lib/garmin.mts";

export default async (req: Request) => {
  const name = new URL(req.url).searchParams.get("name")?.trim();
  if (!name) return corsJson({ error: "Missing profile name" }, 400);

  let body: string;
  try {
    body = await fetchRsc("https://live.garmin.com/" + encodeURIComponent(name));
  } catch (e: any) {
    return corsJson({ error: `Could not reach Garmin: ${e.message}` }, 502);
  }

  // several "garminGuid" objects exist (UI component props); we want the
  // data object — the one that carries the session lists
  let found: any = null;
  let pos = 0;
  for (;;) {
    const i = body.indexOf('{"garminGuid"', pos);
    if (i < 0) break;
    try {
      const obj = extractJsonValue(body, i);
      const guid = obj.garminGuid;
      if (typeof guid === "string" && guid !== "$undefined") {
        if ("activeSessions" in obj || "completedSessions" in obj) return corsJson(obj);
        if (!found) found = obj; // profile exists even if no session data yet
      }
    } catch {
      /* keep scanning */
    }
    pos = i + 1;
  }
  if (found) return corsJson(found);
  return corsJson({ error: `No Garmin Share profile found for '${name}'` }, 404);
};

export const config = { path: "/api/livetrack/sessions" };
