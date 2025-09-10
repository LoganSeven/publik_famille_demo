# Publik Famille Demo (FR + Factures PDF)

Mini-portail **Python/Django** inspiré de *Publik Famille* (Entr'ouvert), entièrement **en français**, avec :
- **paiement par POST** (protégé CSRF) ;
- **génération de facture PDF** (ReportLab) en **euros** ;
- section **Mes documents > Factures** pour télécharger les PDF ;
- **exceptions custom** + **try/except** avec **journaux HTML colorés** ;
- **tests unitaires** et **tests de sécurité** ;
- **SQLite** par défaut (migrations incluses) + **commande bootstrap**.

## 1) Installation (Linux)

```bash
unzip publik_famille_demo.zip
cd publik_famille_demo
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py bootstrap_demo
python manage.py runserver
```

> Tout en un : `./scripts/init_local.sh`

- Portail citoyen : <http://127.0.0.1:8000/> — **parent / parent123**
- Admin : <http://127.0.0.1:8000/admin/> — **admin / admin123**

## 2) Fonctionnalités clés

- **Enfants** (CRUD), **Activités**, **Inscriptions**, **Factures**.
- Paiement **simulé** ou via **API Lingo** → un **PDF** est généré et visible dans **Mes documents > Factures**.
- **Journaux HTML** colorés (gravité) avec vue d’admin.
- **Sécurité** : CSRF, cookies HttpOnly, X-Frame-Options, tests d’accès.
- **UI Material** (Materialize) + CSS custom FR.

## 3) Tests

```bash
python manage.py test
```

## 4) Scripts utiles

- `scripts/init_local.sh` : setup complet + bootstrap + run.
- `scripts/reset_db.sh` : réinitialise la base et réinjecte les données de démo.


## Modes adaptateurs (Local / Lingo / WCS)

- **Facturation (BILLING_BACKEND)** : `local` (par défaut) ou `lingo` (API Lingo pour créer et régler les factures).
  - Variables utiles : `BILLING_LINGO_BASE_URL` (URL de l'API).
- **Inscriptions (ENROLLMENT_BACKEND)** : `local` (par défaut) ou `wcs` (simulation locale structurée pour brancher WCS).
  - Variables utiles : `WCS_BASE_URL` (optionnel).

```bash
# Exemple : activer les modes "lingo" et "wcs"
export BILLING_BACKEND=lingo
export ENROLLMENT_BACKEND=wcs
python manage.py runserver
```

## Logos (branding)
Vous pouvez injecter les logos via URL :
```bash
export EO_LOGO_URL="https://www.entrouvert.com/path/to/logo.svg"
export PUBLIK_LOGO_URL="https://publik.entrouvert.com/path/to/logo.svg"
```
Si non fournis, l'interface reste neutre.
