# ECO Frontend

Enterprise Change Orchestrator — React frontend.

## Prerequisites

- **Node.js 20+**  →  https://nodejs.org
- Backend services running (ports 8000 and 8001)

## Install and start

```bash
cd frontend
npm install
npm run dev
```

Open http://localhost:3000

## Backend proxy

Requests to `/analyze` are proxied to `http://127.0.0.1:8001`.
Requests to `/metadata` are proxied to `http://127.0.0.1:8000`.
Configure in `vite.config.ts`.

## Build for production

```bash
npm run build
# output: frontend/dist/
```
