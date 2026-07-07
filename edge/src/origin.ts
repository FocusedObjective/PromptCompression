import type { EdgeContext, Env } from "./types";

export async function fetchOrigin(
  request: Request,
  env: Env,
  route: string,
  rawBody: string,
  context: EdgeContext
): Promise<Response | null> {
  if (!env.ORIGIN_BASE_URL) {
    return null;
  }

  const timeoutMs = parsePositiveInt(env.ORIGIN_TIMEOUT_MS, 25000);
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort("origin_timeout"), timeoutMs);
  const originUrl = new URL(route, env.ORIGIN_BASE_URL);
  const headers = new Headers(request.headers);
  headers.set("content-type", "application/json");
  headers.set("x-request-id", context.requestId);
  headers.delete("host");

  if (env.ORIGIN_AUTH_MODE === "shared-secret" && env.ORIGIN_SHARED_SECRET) {
    headers.set("x-origin-shared-secret", env.ORIGIN_SHARED_SECRET);
  }

  try {
    const response = await fetch(originUrl, {
      method: "POST",
      headers,
      body: rawBody,
      signal: controller.signal
    });

    if (response.status >= 500) {
      return null;
    }

    const proxiedHeaders = new Headers(response.headers);
    proxiedHeaders.set("x-request-id", context.requestId);
    proxiedHeaders.set("x-edge-decision", "origin");
    proxiedHeaders.set("x-edge-cache", "bypass");
    proxiedHeaders.set("x-edge-ratelimit", context.rateLimit);
    proxiedHeaders.set("x-edge-auth", context.auth);
    proxiedHeaders.set("x-origin-status", String(response.status));
    proxiedHeaders.set("access-control-allow-origin", "*");
    proxiedHeaders.set("access-control-allow-methods", "GET, POST, OPTIONS");
    proxiedHeaders.set("access-control-allow-headers", "authorization, content-type, x-api-key, x-request-id, x-tenant-id");

    return new Response(response.body, {
      status: response.status,
      statusText: response.statusText,
      headers: proxiedHeaders
    });
  } catch {
    return null;
  } finally {
    clearTimeout(timeout);
  }
}

function parsePositiveInt(value: string | undefined, fallback: number): number {
  if (!value) {
    return fallback;
  }
  const parsed = Number.parseInt(value, 10);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
}
