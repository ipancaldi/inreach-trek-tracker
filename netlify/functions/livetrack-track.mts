import { extractJsonValue, fetchRsc, corsJson } from "../lib/garmin.mts";

export default async (req: Request) => {
  const params = new URL(req.url).searchParams;
  const id = params.get("id")?.trim();
  const token = params.get("token")?.trim();
  if (!id || !token) return corsJson({ error: "Missing session id or token" }, 400);

  let body: string;
  try {
    body = await fetchRsc(
      `https://livetrack.garmin.com/session/${encodeURIComponent(id)}/token/${encodeURIComponent(token)}`,
    );
  } catch (e: any) {
    return corsJson({ error: `Could not reach Garmin: ${e.message}` }, 502);
  }

  const points: any[] = [];
  let pos = 0;
  for (;;) {
    const i = body.indexOf('"trackPoints":', pos);
    if (i < 0) break;
    const arrStart = body.indexOf("[", i);
    try {
      for (const tp of extractJsonValue(body, arrStart)) {
        const p = tp.position || {};
        if (typeof p.lat !== "number") continue;
        points.push({
          lat: p.lat,
          lon: p.lon,
          ele: tp.altitude ?? null,
          time: tp.dateTime ?? null,
          speed: tp.speed ?? null,
          course: tp.course ?? null,
        });
      }
    } catch {
      /* keep scanning */
    }
    pos = arrStart + 1;
  }

  // dedupe (pages can overlap) and sort by time
  const seen = new Set<string>();
  const unique = points.filter((p) => {
    const key = `${p.time}|${p.lat}|${p.lon}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
  unique.sort((a, b) => (a.time || "").localeCompare(b.time || ""));

  return corsJson({ points: unique });
};

export const config = { path: "/api/livetrack/track" };
