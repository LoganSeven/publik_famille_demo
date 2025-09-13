# Publik Famille Demo 
[🇬🇧 English version](README_en.md)

---

## Introduction
**Publik Famille Demo** est un portail **démonstratif** Python/Django inspiré de *Publik Famille* (Entr’ouvert). Il a été créé dans le cadre d’une candidature à l’offre 2025 de développeur·euse Python/Django chez Entr’ouvert ([annonce](https://www.entrouvert.com/actualites/2025/embauche-developpeureuse-python-django-2025/)).
Le projet s’efforce d’adopter des méthodes proches de l’écosystème Publik (passerelles, idempotence, sécurité) **sans prétendre être un produit officiel**. Il n’a pas été testé en production et peut contenir des imperfections. Voir la **[TODO list](TODO.md)**.

- Dépôt officiel Entr’ouvert : <https://git.entrouvert.org/entrouvert>
- Dépôt GitHub de cette démo : <https://github.com/LoganSeven/publik_famille_demo>

---

## Fonctionnalités principales
- Paiement **POST-only** (protégé **CSRF**), **405** sur GET.
- **Factures PDF** (ReportLab), visibles dans **Mes documents > Factures**.
- **Vérification d’identité** : simulation locale par défaut, ou OIDC via **Authentic** (production).
- Passerelles configurables :
  - Facturation : **local** ou **lingo** (API Lingo).
  - Inscriptions : **local** ou **wcs** (API WCS).
- **Idempotence** : `get_or_create` + mises à jour conditionnelles.
- **Journaux HTML** colorés (info/warn/error), accessibles en admin.
- **Sécurité** : CSRF, cookies HttpOnly, X-Frame-Options, contrôle d’accès (un parent ne peut payer que ses factures).
- **Tests unitaires** : flux & sécurité (enrollment + payment + CSRF + accès).
- **Commande** `bootstrap_demo` : comptes/données de démo.

---

## Installation (Linux)
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
Accès : Front <http://127.0.0.1:8000/> (**parent/parent123**) · Admin <http://127.0.0.1:8000/admin/> (**admin/admin123**).

---

## Configuration (backends & identité)
**Facturation**  
- `BILLING_BACKEND` = `local` (défaut) | `lingo`  
- `BILLING_LINGO_BASE_URL` (si `lingo`), ex. `http://localhost:8080`

**Inscriptions**  
- `ENROLLMENT_BACKEND` = `local` (défaut) | `wcs`  
- `WCS_BASE_URL`, `WCS_API_TOKEN` (si `wcs`)

**Identité (obligatoire avant inscription)**  
- `IDENTITY_BACKEND` = `simulation` (défaut) | `authentic` (OIDC)  
- `IDENTITY_ENROLL_URL_NAMES` (par défaut : `activities:enroll`)  
- Mode OIDC (Authentic) : `AUTHENTIC_AUTHORIZE_URL`, `AUTHENTIC_TOKEN_URL`, `AUTHENTIC_USERINFO_URL`, `AUTHENTIC_CLIENT_ID`, `AUTHENTIC_CLIENT_SECRET`, `AUTHENTIC_REDIRECT_URI` (optionnel), `AUTHENTIC_DRY_RUN` (tests).

**Exemple (intégration complète)**  
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

## Flux fonctionnels (résumé)
1. **Inscription** `POST /activities/<id>/inscrire/` → création **Enrollment** (PENDING_PAYMENT) + **Invoice** (UNPAID). Si identité non vérifiée : redirection vers **/accounts/verify/** puis reprise.  
2. **Paiement** `POST /billing/payer/<invoice_pk>/` (CSRF requis). GET → **405**. Contrôle d’accès strict (parent propriétaire uniquement). Succès : **Invoice.PAID + paid_on** et **Enrollment.CONFIRMED**, génération du **PDF** et rattachement à **Document**.  
3. **Mes documents > Factures** : liste des PDFs disponibles pour téléchargement.

---

## Tests
```bash
python manage.py test
```
Couvre : flux inscription+paiement, CSRF/POST-only, contrôle d’accès, passerelles **WCS/Lingo** (mocks), vérification d’identité (simulation + OIDC/dry-run).

---

## Dépannage (FAQ)
- **GET /billing/payer/<pk> → 405** : comportement attendu (POST-only).  
- **403 CSRF** : vérifier `{% csrf_token %}` + cookies.  
- **Lingo/WCS non configurés** : définir `BILLING_LINGO_BASE_URL`, `WCS_BASE_URL`, etc.  
- **PDF manquant** : le paiement reste validé; voir logs HTML + admin pour régénérer.  
- **Redirection vérif. identité** : normal si `IDENTITY_BACKEND=simulation` et profil non vérifié.

---

## Branding (logos)
```bash
export EO_LOGO_URL="https://publik.entrouvert.com/media/uploads/2019/10/09/entrouvert-logo_FEIkpEO.png"
export PUBLIK_LOGO_URL="https://publik.entrouvert.com/static/img/logo-publik.png"
```

---

## Remerciements & contexte
Ce projet vise à démontrer des compétences **Python/Django** compatibles avec l’écosystème Entr’ouvert, en s’inspirant de *Publik Famille*.  
Il reste volontairement **ouvert aux améliorations** (voir **[TODO.md](TODO.md)**).

