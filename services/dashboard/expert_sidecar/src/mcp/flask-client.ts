const FLASK_URL = process.env.FLASK_API_URL || "http://localhost:5003";

export async function flaskGet(
  path: string,
  params?: Record<string, string>
): Promise<any> {
  const url = new URL(path, FLASK_URL);
  if (params) {
    Object.entries(params).forEach(([k, v]) => {
      if (v) url.searchParams.set(k, v);
    });
  }
  const resp = await fetch(url.toString(), { signal: AbortSignal.timeout(30000) });
  if (!resp.ok) {
    const body = await resp.text().catch(() => "");
    throw new Error(`Flask API 오류 (${resp.status}): ${body.slice(0, 200)}`);
  }
  return resp.json();
}

export async function flaskPost(
  path: string,
  body: Record<string, unknown>
): Promise<any> {
  const url = new URL(path, FLASK_URL);
  const resp = await fetch(url.toString(), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal: AbortSignal.timeout(30000),
  });
  if (!resp.ok) {
    const text = await resp.text().catch(() => "");
    throw new Error(`Flask API 오류 (${resp.status}): ${text.slice(0, 200)}`);
  }
  return resp.json();
}
