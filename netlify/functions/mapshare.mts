// Proxy for the classic MapShare KML feed (older inReach accounts).

export default async (req: Request) => {
  const params = new URL(req.url).searchParams;
  const name = params.get("name")?.trim();
  const password = params.get("password") || "";
  const d1 = params.get("d1") || "";
  const d2 = params.get("d2") || "";

  const err = (status: number, message: string) =>
    new Response(JSON.stringify({ error: message }), {
      status,
      headers: { "Content-Type": "application/json", "Access-Control-Allow-Origin": "*" },
    });

  if (!name) return err(400, "Missing MapShare name");

  const url = new URL("https://share.garmin.com/Feed/Share/" + encodeURIComponent(name));
  if (d1) url.searchParams.set("d1", d1);
  if (d2) url.searchParams.set("d2", d2);

  const headers: Record<string, string> = { "User-Agent": "inReach-Trek-Tracker/1.0" };
  if (password) {
    // Password-protected MapShare uses Basic auth with an empty username
    headers["Authorization"] = "Basic " + btoa(":" + password);
  }

  let r: Response;
  try {
    r = await fetch(url, { headers });
  } catch (e: any) {
    return err(502, `Could not reach Garmin: ${e.message}`);
  }
  if (r.status === 401) return err(401, "MapShare is password-protected or the password is wrong");
  if (!r.ok) return err(502, `Garmin returned HTTP ${r.status}`);

  return new Response(await r.arrayBuffer(), {
    status: 200,
    headers: {
      "Content-Type": "application/vnd.google-earth.kml+xml",
      "Access-Control-Allow-Origin": "*",
      "Cache-Control": "no-store",
    },
  });
};

export const config = { path: "/api/mapshare" };
