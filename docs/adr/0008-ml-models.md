# ADR 0008 — Modèles de ML : baselines d'abord, modèles simples, seuils d'historique

- **Statut** : accepté
- **Date** : 2026-10-13

## Contexte
Trois besoins : estimer l'heure d'arrivée (ETA) d'un vol en cours, prévoir le trafic par zone,
repérer les vols atypiques. Contraintes : l'historique démarre à zéro (il s'accumule avec
l'ingestion), l'ADS-B ne contient ni origine ni destination (elles sont reconstituées par dbt),
tout doit tourner sur un poste de développement, et chaque modèle doit **prouver qu'il bat une
baseline simple**.

## Décisions

| Besoin | Modèle | Baseline | Évaluation |
|---|---|---|---|
| ETA | **LightGBM** qui apprend une **correction multiplicative** de la baseline (cible = durée réelle / durée naïve, bornée à [0,2 ; 5]) | distance restante (haversine jusqu'à l'aéroport d'arrivée) / vitesse sol | MAE (min) et MAPE, split **temporel par vol** (les 20 % de vols les plus récents en test) |
| Trafic par zone | **Holt-Winters** (statsmodels, saisonnalité 24 h) dès 48 h d'historique, Holt amorti avant | naïf saisonnier (même heure la veille) ou dernière valeur | MAE sur les dernières heures (*holdout*), puis réentraînement complet et prévision à 6 h |
| Vols atypiques | **Isolation Forest** (RobustScaler + 200 arbres, contamination 1 %) sur des caractéristiques de trajectoire (rectitude, taux de virage, altitude max, vitesse moyenne…) | — (non supervisé) | explication par z-score robuste des caractéristiques les plus éloignées de la médiane |
| Déroutements | règle dbt : arrivée différente de la destination habituelle du callsign (≥ 3 vols, part ≥ 60 %) | — | publiés comme alertes `diversion` |
| Terrains hors référentiel | **DBSCAN** (métrique haversine, eps 3 km, 3 points min.) sur les décollages / atterrissages à plus de 15 km de tout aéroport retenu | — | part des points regroupés ; chaque cluster est étiqueté avec l'entrée OurAirports la plus proche, tous types (aérodrome, héliport…) |

- **Correction de la baseline plutôt que régression directe** : sur le lake de test, LightGBM
  appris directement sur la durée restante faisait bien *pire* que la physique (MAE 4,7 min
  contre 1,7 min) ; en apprenant le ratio, il reste proche de la baseline quand il ne sait
  rien et apprend ce qu'elle ignore (descente, approche, attente) : MAE 0,21 min.
- **Vent en altitude (Open-Meteo)** : la vitesse sol contient déjà le vent *actuel* ; le
  vent arrière à 250 hPa (grille de 10°, horaire) n'apporte de l'information que s'il varie
  le long de la route. Plutôt que de le supposer, l'entraînement réentraîne le même modèle
  sans cette variable et logue `mae_min_without_wind` : la variable doit prouver son utilité.
  Sur le lake de test (vols synthétiques sans vent), l'écart est nul, comme attendu.
- **DBSCAN plutôt que k-means** : le nombre de terrains est inconnu et le bruit (perte de
  signal à basse altitude) doit rester hors cluster ; DBSCAN avec la distance haversine ne
  demande qu'un rayon physique (3 km) et un effectif minimal.
- **Seuils d'historique** (`ML_ETA_MIN_FLIGHTS=200`, 12 h de série pour la prévision,
  50 vols pour l'Isolation Forest) : en dessous, l'asset Dagster se termine en `skipped` avec
  la raison, et l'API sert l'ETA naïve.
- **Barrière de promotion** : un modèle d'ETA qui ne bat pas la baseline sur le jeu de test
  est loggué dans MLflow mais pas servi ; le modèle précédemment servi est retiré et l'API
  revient à l'estimation naïve.
- **Prophet écarté** : dépendance lourde (cmdstan) pour un gain nul sur des séries horaires
  de quelques jours ; statsmodels suffit et s'installe partout.
- **Suivi** : chaque entraînement logue paramètres, métriques du modèle *et* de la baseline
  dans MLflow (registry `skyradar-eta`) et ajoute une ligne à `ml_model_metrics.parquet`,
  lu par le dashboard Superset « Modèles ML ».
- **Service** : l'API charge `eta_model.txt` (format natif LightGBM) depuis le volume des
  marts et le recharge quand il change (vérification toutes les minutes) ; pas de serveur de
  modèles : une prédiction pour un avion se fait en mémoire, dans le processus de l'API.

## Conséquences
- Les modèles se mettent en route d'eux-mêmes après quelques heures (prévision, outliers) à
  quelques jours (ETA) d'ingestion ; le CI les entraîne sur le lake de test déterministe et
  vérifie que l'ETA bat la baseline.
- Les métriques affichées dans le README sur données synthétiques ne préjugent pas des
  performances sur données réelles (vent, attentes, couverture partielle).
