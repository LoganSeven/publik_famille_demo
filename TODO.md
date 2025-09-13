# TODO – Publik Famille Demo

> Liste des chantiers restants et améliorations prévues (réaliste et priorisée).

##  Backend / Sécurité
- [ ] Finaliser l’intégration **Authentic (OIDC)** en conditions réelles (callbacks, erreurs provider, rafraîchissement token).
- [ ] Étendre la passerelle **WCS** : synchronisation bidirectionnelle, mapping de statuts distants, reprise sur erreur.
- [ ] Résilience réseau : timeouts configurables, retries exponentiels, journalisation des payloads (masqués).
- [ ] Support **PostgreSQL** (settings + migrations + fixtures dédiées) en plus de SQLite.
- [ ] CI/CD **GitHub Actions** : lint (flake8), mypy, tests, build docs Sphinx.

##  Frontend / UX
- [ ] Améliorer l’UI Materialize : responsive avancé, messages d’erreur/succès contextualisés, feedbacks asynchrones.
- [ ] Sections documents : prévisualisation PDF, pagination/tri, upload d’autres documents (justificatifs, etc.).
- [ ] Accessibilité (a11y) : contraste, navigation clavier, aria-labels.

##  Tests / Qualité
- [ ] Couverture de tests accrue : scénarios d’échec (réseaux/timeout), tests d’intégration identité (simulation/oidc) bout-en-bout.
- [ ] Tests de charge basiques sur paiement/inscription.
- [ ] Ajout de **factory-boy** / **pytest** pour un setup plus concis.

##  Ops / Observabilité
- [ ] Logs structurés (JSON) en plus des journaux HTML, export vers filebeat/ELK.
- [ ] Ajout de métriques (Prometheus) : compteurs d’inscriptions, paiements, erreurs.
- [ ] Paramétrage fin des niveaux de logs par module.

##  Documentation
- [ ] Générer la doc **Sphinx** (autodoc + napoleon + viewcode) et publier (gh-pages).
- [ ] Schémas d’architecture : flux **inscription/paiement/identité**, passerelles **Lingo/WCS**, middleware identité.
- [ ] Captures d’écran UI dans README.

##  Divers
- [ ] Exposer les variables **IDENTITY_* ** dans les templates via un context processor dédié (actuellement seuls logos/backends sont exposés).
- [ ] Petites dettes techniques : homogénéiser les messages, centraliser les constantes d’URL, traductions i18n complètes.
