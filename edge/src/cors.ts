const ALLOWED_REQUEST_HEADERS = [
  "authorization",
  "cache-control",
  "content-type",
  "x-api-key",
  "x-request-id",
  "x-tenant-id"
];

const EXPOSED_RESPONSE_HEADERS = [
  "retry-after",
  "x-compression-cache",
  "x-compression-content-cache",
  "x-compression-policy",
  "x-edge-auth",
  "x-edge-cache",
  "x-edge-decision",
  "x-edge-ratelimit",
  "x-origin-status",
  "x-request-id"
];

export const CORS_ALLOW_HEADERS = ALLOWED_REQUEST_HEADERS.join(", ");
export const CORS_EXPOSE_HEADERS = EXPOSED_RESPONSE_HEADERS.join(", ");

export function applyCorsResponseHeaders(headers: Headers): void {
  headers.set("access-control-allow-origin", "*");
  headers.set("access-control-expose-headers", CORS_EXPOSE_HEADERS);
}

export function applyCorsPreflightHeaders(headers: Headers): void {
  applyCorsResponseHeaders(headers);
  headers.set("access-control-allow-methods", "GET, POST, OPTIONS");
  headers.set("access-control-allow-headers", CORS_ALLOW_HEADERS);
  headers.set("access-control-max-age", "86400");
}
