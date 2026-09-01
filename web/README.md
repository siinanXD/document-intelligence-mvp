# Cockpit

Next.js App Router UI for the document intelligence MVP. It calls the FastAPI
API; it does not contain backend business logic or provider secrets.

See [`docs/COCKPIT.md`](../docs/COCKPIT.md) for the demo path and commands.

```bash
npm install
npm run dev          # http://127.0.0.1:3000
npm run lint
npm run typecheck
npm test
```

`NEXT_PUBLIC_API_BASE_URL` defaults to `http://127.0.0.1:8000`.
