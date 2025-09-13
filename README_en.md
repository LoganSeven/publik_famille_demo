# Publik Famille Demo
[🇫🇷 Version française](README.md)

---

## Introduction
**Publik Famille Demo** is a **demonstration** Python/Django portal inspired by Entr’ouvert’s *Publik Famille*. It was built in the context of applying to Entr’ouvert’s 2025 Python/Django developer position ([job posting](https://www.entrouvert.com/actualites/2025/embauche-developpeureuse-python-django-2025/)).
It follows patterns close to the Publik ecosystem (gateways, idempotency, security) **without claiming to be an official product**. Not production-tested; imperfections may remain. See **[TODO.md](TODO.md)**.

- Entr’ouvert official repositories: <https://git.entrouvert.org/entrouvert>
- This demo’s GitHub repo: <https://github.com/LoganSeven/publik_famille_demo>

---

## Key features
- **POST-only** payments (CSRF-protected), **405** on GET.
- **PDF invoices** (ReportLab), available under **My documents > Invoices**.
- **Identity verification**: local simulation by default, or **Authentic** OIDC (production).
- Configurable gateways:
  - Billing: **local** or **lingo** (Lingo API).
  - Enrollment: **local** or **wcs** (WCS API).
- **Idempotency**: `get_or_create` + conditional updates.
- **HTML logs** with color coding (info/warn/error), admin-accessible.
- **Security**: CSRF, HttpOnly cookies, X-Frame-Options, access control.
- **Unit tests** for flows & security.
- **`bootstrap_demo`** command: demo accounts & data.

---

## Quick install (Linux)
```bash
git clone https://github.com/LoganSeven/publik_famille_demo.git
cd publik_famille_demo
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py bootstrap_demo
python manage.py runserver
```
Access: Front <http://127.0.0.1:8000/> (**parent/parent123**) · Admin <http://127.0.0.1:8000/admin/> (**admin/admin123**).

---

## Configuration (backends & identity)
**Billing**  
- `BILLING_BACKEND` = `local` (default) | `lingo`  
- `BILLING_LINGO_BASE_URL` (if `lingo`), e.g., `http://localhost:8080`

**Enrollment**  
- `ENROLLMENT_BACKEND` = `local` (default) | `wcs`  
- `WCS_BASE_URL`, `WCS_API_TOKEN` (if `wcs`)

**Identity (required before enrollment)**  
- `IDENTITY_BACKEND` = `simulation` (default) | `authentic` (OIDC)  
- `IDENTITY_ENROLL_URL_NAMES` (default: `activities:enroll`)  
- OIDC (Authentic): `AUTHENTIC_AUTHORIZE_URL`, `AUTHENTIC_TOKEN_URL`, `AUTHENTIC_USERINFO_URL`, `AUTHENTIC_CLIENT_ID`, `AUTHENTIC_CLIENT_SECRET`, `AUTHENTIC_REDIRECT_URI` (optional), `AUTHENTIC_DRY_RUN` (tests).

**Example (full integration)**  
```bash
export BILLING_BACKEND=lingo
export BILLING_LINGO_BASE_URL=http://localhost:8080
export ENROLLMENT_BACKEND=wcs
export WCS_BASE_URL=http://localhost:9090
export WCS_API_TOKEN=dev-token
export IDENTITY_BACKEND=authentic
export AUTHENTIC_AUTHORIZE_URL=https://idp.example/authorize
export AUTHENTIC_TOKEN_URL=https://idp.example/token
export AUTHENTIC_USERINFO_URL=https://idp.example/userinfo
export AUTHENTIC_CLIENT_ID=demo
export AUTHENTIC_CLIENT_SECRET=secret
python manage.py runserver
```

---

## Functional flows (overview)
1. **Enrollment** `POST /activities/<id>/inscrire/` → create **Enrollment** (PENDING_PAYMENT) + **Invoice** (UNPAID). If identity not verified: redirect to **/accounts/verify/** then resume.  
2. **Payment** `POST /billing/payer/<invoice_pk>/` (CSRF required). GET → **405**. Strict access control (owner parent only). Success: **Invoice.PAID + paid_on** and **Enrollment.CONFIRMED**, generate **PDF** and attach to **Document**.  
3. **My documents > Invoices**: list and download PDFs.

---

## Tests
```bash
python manage.py test
```
Covers: enrollment+payment flow, CSRF/POST-only, access control, **WCS/Lingo** gateways (mocks), identity verification (simulation + OIDC/dry-run).

---

## Troubleshooting (FAQ)
- **GET /billing/payer/<pk> → 405**: expected (POST-only).  
- **403 CSRF**: ensure `{% csrf_token %}` + cookies.  
- **Unconfigured Lingo/WCS**: define `BILLING_LINGO_BASE_URL`, `WCS_BASE_URL`, etc.  
- **Missing PDF**: payment still valid; check HTML logs & admin to regenerate.  
- **Identity verification redirect**: normal if `IDENTITY_BACKEND=simulation` and profile unverified.

---

## Branding (logos)
```bash
export EO_LOGO_URL="https://publik.entrouvert.com/media/uploads/2019/10/09/entrouvert-logo_FEIkpEO.png"
export PUBLIK_LOGO_URL="https://publik.entrouvert.com/static/img/logo-publik.png"
```

---

## Thanks & context
This demo showcases **Python/Django** skills aligned with the Entr’ouvert ecosystem, inspired by *Publik Famille*.  
It is intentionally **open to improvements** (see **[TODO.md](TODO.md)**).
