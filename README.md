# Publik Famille Demo (FR + Factures PDF)

Mini-portail **Python/Django** inspiré de *Publik Famille* (Entr'ouvert), entièrement **en français**, avec :
- **paiement par POST** (protégé **CSRF**) ;
- **génération de facture PDF** (ReportLab) en **euros** ;
- section **Mes documents > Factures** pour télécharger les PDF ;
- **exceptions custom** + **try/except** avec **journaux HTML colorés** ;
- **tests unitaires** (flux + sécurité) ;
- **SQLite** par défaut (migrations incluses) + **commande bootstrap** ;
- **passerelles idempotentes** (Local/Lingo) et **inscription confirmée après paiement** ;
- **accès paresseux** à `Enrollment.invoice` (créée à la première utilisation).

---

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

---

## 2) Fonctionnalités clés

- **Enfants** (CRUD), **Activités**, **Inscriptions**, **Factures**.
- Paiement **local simulé** ou via **API Lingo** → un **PDF** est généré et visible dans **Mes documents > Factures**.
- **Journaux HTML** colorés (niveaux info/warn/error) avec vue d’admin.
- **Sécurité** : CSRF systématique sur les POST sensibles, cookies HttpOnly, X-Frame-Options, tests d’accès.
- **UI Material** (Materialize) + CSS FR.

---

## 3) Modes adaptateurs (Local / Lingo / WCS)

### Facturation (`BILLING_BACKEND`)
- `local` (par défaut) : crée et règle en base locale.
- `lingo` : appelle l’API Lingo pour créer/payer, puis **synchronise** la facture locale.

Variables utiles :
- `BILLING_LINGO_BASE_URL` : URL base de l’API Lingo (ex. `http://localhost:8080`).

### Inscriptions (`ENROLLMENT_BACKEND`)
- `local` (par défaut) : crée l’inscription localement.
- `wcs` : passerelle structurée pour simuler/brancher un WCS.

Variables utiles :
- `WCS_BASE_URL` (optionnel), `WCS_API_TOKEN` (optionnel).

Activation exemple :

```bash
export BILLING_BACKEND=lingo
export BILLING_LINGO_BASE_URL=http://localhost:8080
export ENROLLMENT_BACKEND=wcs
export WCS_BASE_URL=http://localhost:9090
export WCS_API_TOKEN=dev-token
python manage.py runserver
```

---

## 4) Flux fonctionnels (résumé)

1. **Inscription**  
   - `POST /activities/<id>/inscrire/` (CSRF requis)  
   - La passerelle d’inscription (`local` ou `wcs`) **crée** l’inscription.  
   - La passerelle de facturation (`local` ou `lingo`) **crée la facture** (statut **UNPAID**).  
   - Redirection vers **Mes inscriptions** avec message de succès.

2. **Paiement**  
   - `POST /billing/payer/<invoice_pk>/` (CSRF requis, **GET interdit → 405**).  
   - Contrôle d’accès : **seul le parent** de l’enfant peut payer. Sinon **302** avec message d’erreur.  
   - En mode `lingo`, on appelle l’API distante pour marquer payé.  
   - En cas de succès :
     - La facture passe à **PAID** (+ `paid_on`).  
     - L’**inscription passe à CONFIRMED**.  
     - Un **PDF** est généré et **rattaché** (Mes documents > Factures).

3. **Mes documents > Factures**  
   - Liste des documents ; chaque facture payée a son PDF téléchargeable.

---

## 5) Détails techniques importants

### a) Création de facture **idempotente**
- **LocalBillingGateway.create_invoice** et **LingoGateway.create_invoice** utilisent `get_or_create` puis mettent à jour les champs au besoin (`amount`, `lingo_id`).  
- Empêche les **doublons** lors d’appels répétés ou de re-soumissions.

### b) Confirmation **automatique** après paiement
- `mark_paid` (Local/Lingo) met à jour la facture **et** l’inscription liée : `Enrollment.status = CONFIRMED`.

### c) Accès paresseux à `Enrollment.invoice`
- `Enrollment.invoice` est une **propriété** Python (shim) qui fait un `get_or_create` si la facture n’existe pas encore (montant = `activity.fee`).  
- Évite les `RelatedObjectDoesNotExist` dans les tests, vues et templates.

### d) Sécurité & HTTP
- **Paiement par POST uniquement** (CSRF obligatoire) → GET renvoie **405 Method Not Allowed**.  
- Tentative de paiement d’une facture d’autrui → redirection **302** + message *Accès refusé*.  
- Journalisation **HTML colorée** (fallback sur `logging` Python si HTML logger indisponible).

---

## 6) Tests

```bash
python manage.py test
```

Les tests couvrent :
- Création d’inscription + génération facture + paiement → **PDF** généré et **document** attaché.
- **POST-only** et **CSRF** sur la route de paiement.
- Contrôle d’accès : un utilisateur ne peut pas payer la facture d’autrui.
- Passerelles `wcs` et `lingo` simulées via mocks réseau ; erreurs levées si config manquante.

---

## 7) Commandes utiles

- `scripts/init_local.sh` : setup complet + bootstrap + run.  
- `scripts/reset_db.sh` : réinitialise la base et réinjecte les données de démo.  
- `python manage.py bootstrap_demo` : compte **parent/admin** + données d’exemple.

---

## 8) Dépannage (FAQ)

- **GET sur `/billing/payer/<pk>/`** → **405** : normal. Utiliser **POST** avec CSRF.  
- **403 CSRF** en POST → activer les cookies, vérifier le token `{% csrf_token %}` dans le formulaire.  
- **Env Lingo non configurée** et backend sur `lingo` → `BILLING_LINGO_BASE_URL` est **obligatoire**.  
- **Aucune facture visible** dans “Mes inscriptions” : l’attribut `invoice` est paresseux → il apparaît au premier accès/usage (création automatique).  
- **PDF absent mais paiement OK** : message si la génération a échoué ; le paiement **reste** enregistré. Regarder les logs (HTML + console).

---

## 9) Changelog (dernière mise à jour)

- **Idempotence** : `create_invoice` (Local/Lingo) passe à `get_or_create` + update conditionnel.  
- **Paiement** : `mark_paid` confirme automatiquement l’inscription (`Enrollment.Status.CONFIRMED`).  
- **Robustesse tests** : `Enrollment.invoice` devient une **propriété paresseuse** (évite les `RelatedObjectDoesNotExist`).  
- **Sécurité** : ré-affirmation du **POST-only** + **CSRF** sur le paiement.  
- **Docs** : README enrichi (modes, flux, FAQ).

---

## 10) Logos (branding)

Vous pouvez injecter des logos via variables d’environnement :

```bash
export EO_LOGO_URL="https://publik.entrouvert.com/media/uploads/2019/10/09/entrouvert-logo_FEIkpEO.png"
export PUBLIK_LOGO_URL="https://publik.entrouvert.com/static/img/logo-publik.png"
```

Sans ces variables, l’interface reste neutre.
