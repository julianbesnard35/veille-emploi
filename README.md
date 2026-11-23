# Veille emploi — collecte automatique

Chaque matin (~6h15 heure de Paris), GitHub Actions lance `collect.py` pour deux profils de recherche.
Le script récupère les offres récentes (≤ 6 jours) sur **LinkedIn, HelloWork, APEC, Guichet-Emplois (Canada)**
et **Business France VIE/VIA**, puis les enregistre dans `data/latest/` :

| Fichier | Contenu |
|---|---|
| `data/latest/profil_a.json` | offres brutes du profil A + statut de chaque site |
| `data/latest/profil_b.json` | offres brutes du profil B + statut de chaque site |
| `data/latest/vie_via.json` | toutes les offres VIE/VIA publiées ces 6 derniers jours (pour les deux) |
| `data/latest/_date.json` | date de la collecte |

Les tâches planifiées Claude  lisent ces fichiers, puis notent, trient et envoient
les rapports. Aucun ordinateur n'a besoin d'être allumé.

- Changer les recherches : modifier `config.json`.
- Relancer à la main : onglet **Actions** → « Collecte quotidienne des offres » → **Run workflow**.
- Les archives des 7 derniers jours sont dans `data/archive/`.
