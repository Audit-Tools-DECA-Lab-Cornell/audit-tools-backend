## YEE Frontend (Next.js)

Minimal frontend starter for the Youth Enabling Environments Audit Tool.

### Prerequisites

- Backend running at `http://127.0.0.1:8000`
- Node.js 20+

### Run locally

```bash
cd yee-frontend
npm install
npm run dev
```

Open: `http://127.0.0.1:3000`

### Backend connection

This frontend calls local Next.js API routes (`/api/yee/*`) that proxy to the backend.

Configure backend URL if needed:

```bash
API_BASE_URL=http://127.0.0.1:8000
```

### Flow

1. Loads instrument from `GET /api/yee/instrument`
2. Submits responses to `POST /api/yee/audits`
3. Displays total and section scores from the response
