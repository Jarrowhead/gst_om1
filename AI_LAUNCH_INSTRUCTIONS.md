# GST Filing App — AI Agent Launch & Login Guide

> **Target Repository:** `D:\gst_app_latest`  
> **Status:** Fully functional & verified.

---

## ⚡ Quickest AI Action: Launch Everything in One Command

When the user asks to **"launch the app"**, **"start the GST app"**, or **"launch and login"**:

Run this single command using `run_command` with `IsDaemon: true`:

```powershell
& "D:\gst_app_latest\backend\.venv\Scripts\python.exe" "D:\gst_app_latest\scripts\launch_app.py"
```

*(Alternatively, run `D:\gst_app_latest\launch.bat` or `D:\gst_app_latest\launch.ps1`).*

### What this launcher automatically handles:
1. **PostgreSQL 16 (:5436):** Cleans any stale `postmaster.pid`, boots postgres, verifies schemas `core`, `gst`, `extraction`.
2. **Redis (:6380):** Boots redis-server without crashing `--save ""` flag.
3. **MinIO Object Store (:9001):** Boots MinIO native server, sets up `gst-docs` bucket.
4. **Database Migrations:** Executes `alembic upgrade head` cleanly.
5. **FastAPI Backend (:8084):** Starts uvicorn factory and waits for health check `200 OK`.
6. **Demo Data Seed:** Seeds `demo.tony@example.com` and 4 checksum-valid GSTINs.
7. **Next.js Frontend (:9094):** Starts `next dev` and waits until `http://127.0.0.1:9094/login` answers 200.
8. **Process Supervision:** Keeps all services alive in the background.

---

## 🔐 How to Log In (Browser or API)

### 1. Browser Login Flow (UI)
1. **Navigate to:** `http://127.0.0.1:9094/login`
2. **Email identifier:** `demo.tony@example.com`
3. Click **"Send code"** (`data-testid="login-request-otp"`).
4. The screen immediately displays the development OTP:
   ```html
   <p data-testid="dev-otp">dev code: 123456</p>
   ```
5. Enter the 6-digit code into the code input (`data-testid="login-otp"`).
6. Click **"Verify & sign in"** (`data-testid="login-verify"`).
7. The page navigates to `http://127.0.0.1:9094/app` (Dashboard with accounts and returns ready).

### 2. API Login (Direct Token)
```bash
# Request OTP (dev_otp is in response body):
curl -X POST http://127.0.0.1:8084/api/v1/auth/otp/request \
  -H "Content-Type: application/json" \
  -d '{"identifier":"demo.tony@example.com","purpose":"LOGIN"}'

# Verify OTP:
curl -X POST http://127.0.0.1:8084/api/v1/auth/otp/verify \
  -H "Content-Type: application/json" \
  -d '{"identifier":"demo.tony@example.com","otp":"<DEV_OTP>"}'
```

---

## 🛠️ Service Architecture & Port Ledger

| Service | Port | Endpoint / Target | Notes |
|---|---|---|---|
| **Frontend** | `9094` | `http://127.0.0.1:9094/login` | Next.js 16 (proxies `/api/v1` to 8084) |
| **Backend** | `8084` | `http://127.0.0.1:8084/api/v1/health` | FastAPI uvicorn factory |
| **PostgreSQL** | `5436` | `127.0.0.1:5436` / `gst_filing_db` | User: `gst`, Pass: `gst_dev_pass` |
| **Redis** | `6380` | `127.0.0.1:6380` | Port 6380 (rate limiting, queue, OTP) |
| **MinIO** | `9001` | `127.0.0.1:9001` (Console: `9002`) | User: `gst_admin`, Pass: `gst_minio_dev_pass` |

---

## 🚨 Troubleshooting & Caveats

- **OTP Rate Limit Exceeded:**  
  OTP is limited to 5 requests per hour. If rate-limited, clear it with:
  ```powershell
  & "D:\farmer_app\tools\redis\redis-cli.exe" -p 6380 del "otp_rl:demo.tony@example.com"
  ```
- **Stale Port Conflict:**  
  Check listening ports:
  ```powershell
  Get-NetTCPConnection -LocalPort 5436,6380,9001,8084,9094 -ErrorAction SilentlyContinue | Select-Object LocalPort, State, OwningProcess
  ```
  Kill by PID if needed:
  ```powershell
  Stop-Process -Id <PID> -Force
  ```
