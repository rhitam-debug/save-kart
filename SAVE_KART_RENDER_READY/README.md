# SAVE KART — Night Canteen MVP

A local-first QR ordering and kitchen-management MVP for a single pilot canteen.

## Included
- Student menu, cart, table QR validation, demo checkout and order tracking
- Kitchen PIN login, oldest-first active order queue, order status and ETA updates
- Owner PIN login, daily sales summary, popular items, ratings, feedback and CSV export
- Ten unique table tokens and QR SVG endpoints (`/qr/1.svg` ... `/qr/10.svg`)
- SQLite persistence

## Important limitations
This is a **pilot MVP**, not a production payment platform.
- Checkout is explicitly DEMO mode. It does not collect money or integrate a payment gateway.
- CSV export is included. Google Sheets API sync is not configured.
- PIN-based staff login is a starter gate, not a complete user/role management system.
- SQLite is suitable for a single-instance pilot; use PostgreSQL and tenant isolation before multi-canteen SaaS rollout.
- Customer notifications are in-app polling; web push / SMS / WhatsApp is not integrated.
- The sample menu and prices are placeholders.

## Deploy on Render (demo)

1. Push this folder to a GitHub repository.
2. In Render, choose **New → Web Service** and connect the repository.
3. Set **Root Directory** to `.` if these files are at the repository root.
4. Build command: `pip install -r requirements.txt`
5. Start command: `gunicorn app:app`
6. Add environment variables: `SECRET_KEY` (a long random value), `ADMIN_PIN` (new owner PIN), `KITCHEN_PIN` (new kitchen PIN), and `PUBLIC_BASE_URL` (the exact deployed `https://...onrender.com` URL).
7. Deploy, then open `/qr/1.svg` through `/qr/10.svg` to download the table QR codes.

**Free-hosting warning:** Render Free services use an ephemeral filesystem and can spin down. This MVP currently uses SQLite, so orders, menu edits and other database changes can be lost after a restart or deploy. Table QR tokens are deterministic when `SECRET_KEY` stays unchanged, so the printed QR codes remain valid after a database reset. Use persistent storage and a production database before a live canteen pilot.

## Run locally (Windows PowerShell)
```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
py app.py
```
Open `http://127.0.0.1:5000`.

Default demo PINs:
- Kitchen: `1357`
- Owner: `2468`

Change both before exposing the app to any network. For a quick local test, QR URLs point to `http://127.0.0.1:5000`. For physical phones to scan them, set `PUBLIC_BASE_URL` to your computer's reachable LAN address or deployed HTTPS domain before starting the app.

## QR codes
Open `/qr/1.svg` through `/qr/10.svg`. Each code includes a unique table token. Print and attach the correct QR to each table.

## Before a real pilot
1. Set a strong `SECRET_KEY`, `ADMIN_PIN`, and `KITCHEN_PIN`.
2. Use HTTPS and a persistent database volume.
3. Add proper staff accounts, password hashing, CSRF protection, rate limiting and tenant-scoped authorization.
4. Integrate a payment gateway using server-created payment orders and verified signed webhooks. Never trust client-side payment success.
5. Add transactional outbox/retry for notifications and payment events.
6. Set up backups, privacy notice, refund/cancellation policy and owner permissions.
7. Obtain canteen/college approval and define data ownership.

## API outline
- `GET /api/menu`
- `GET /api/table/<table_no>`
- `POST /api/order`
- `GET /api/order/<id>`
- `POST /api/review`
- `POST /api/complaint`
- `POST /api/login`
- `GET /api/kitchen/orders`
- `PATCH /api/kitchen/order/<id>`
- `GET /api/admin/summary`
- `POST /api/admin/menu`
- `GET /api/admin/export.csv`
- `GET /qr/<table_no>.svg`
