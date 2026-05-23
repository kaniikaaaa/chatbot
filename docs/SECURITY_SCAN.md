# Security Scan

Fixed/verified:

- Backend APIs require bearer auth except `/healthz`.
- Ingestion writes require `X-Ingestion-Key`.
- Auth comparisons use `hmac.compare_digest`.
- UUIDs validate at request boundaries.
- `.env` is ignored and excluded from Docker build context.
- Frontend does not expose the API auth token in `NEXT_PUBLIC_*`; server proxy routes inject auth at runtime.
- Docker frontend build uses `npm ci`.
