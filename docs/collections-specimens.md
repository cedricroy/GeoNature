# Spécimens en collection : analyse et pistes

Note faite le 2026-11-05 à la suite de l'issue GeoMys [ANA-CEN-Ariege/GeoMys-android#54](https://github.com/ANA-CEN-Ariege/GeoMys-android/issues/54) « Module de gestion/saisie des spécimens en collection ».

## Le besoin

Il porte sur les invertébrés conservés en collection (épinglés ou en alcool).

- **Saisir des données à partir des étiquettes** des spécimens, y compris des collections reçues pour étude. Les étiquettes « station » (commune, lieu-dit, GPS, date, observateur) et les étiquettes « identité » (taxon) sont souvent séparées. Idéalement on part d'une photo de l'étiquette.
- **Produire des étiquettes** à partir des données saisies sur le terrain : export tableur → publipostage Word, avec les UUID des données pour faire le lien entre le spécimen et la donnée naturaliste.

## `gn_module_individuals` ne convient pas

[PnX-SI/gn_module_individuals](https://github.com/PnX-SI/gn_module_individuals) : branches `master` (0.0.1) et `develop`, lues en novembre 2026, GeoNature ≥ 2.16.

- **Objet** : le suivi d'animaux **vivants** identifiés individuellement (capture-marquage-recapture, balises GPS/GSM/VHF, marques couleur).
- **Modèle de données** :
  - `gn_monitoring.t_individuals` : nom, cd_nom, sexe, actif, données additionnelles ;
  - `gn_individual.t_tracking_devices` : type de dispositif (nomenclature `TYPE_DISPO_SUIVI`), fournisseur, code ;
  - `gn_individual.t_individual_deployments` : pose d'un dispositif ou d'une marque sur un individu (nomenclature `LOC_DISPO_SUIVI`), dates de pose et de retrait.
- Il est relié à Occtax et Monitoring (`cor_individual_module`) pour les observations et recaptures.
- **Il n'y a pas** de notion de spécimen, de lot, de lieu de conservation, d'étiquette, de numéro d'inventaire de collection, ni d'export d'étiquettes.

On le garde pour le suivi d'individus vivants. On ne le détourne pas pour les collections.

## Ce qu'Occtax et la Synthèse permettent déjà

Les champs du standard Occtax SINP, au niveau de l'occurrence (`pr_occtax.t_occurrences_occtax`) puis dans `gn_synthese.synthese` :

| Champ | Usage pour une collection |
|---|---|
| `id_nomenclature_exist_proof` (`PREUVE_EXIST`) | « Non numérique » = un spécimen est conservé |
| `non_digital_proof` | n° ou code du spécimen, nom de la collection, lieu de conservation (texte libre) |
| `digital_proof` | URL d'une photo du spécimen ou de l'étiquette |
| `id_nomenclature_determination_method` / `determiner` | méthode (examen des genitalia, etc.) et déterminateur |
| champs additionnels (`additional_fields`) | si un champ structuré dédié est nécessaire, voir `occtax-additional-fields.md` |

Pour faire le lien entre l'étiquette et la donnée, on utilise les UUID SINP déjà présents :

- `unique_id_sinp_grp` pour le relevé, c'est-à-dire la station ;
- `unique_id_occurence_occtax` pour l'occurrence, c'est-à-dire le taxon ;
- `unique_id_sinp_occtax` pour le dénombrement.

L'application GeoMys les génère côté terrain et les envoie. Ils peuvent donc être imprimés sur les étiquettes **avant** la synchronisation.

## Répartition retenue

1. **GeoMys (Android)** :
   - export d'étiquettes par sortie (CSV `;` UTF-8 avec BOM), en trois types : station / station + taxon / station + taxon + dénombrement ;
   - case « spécimen collecté » qui remplit `PREUVE_EXIST` et `non_digital_proof` ;
   - ensuite, saisie assistée par lecture des photos d'étiquettes sur le téléphone, sans réseau.
   - Détail dans `NOTES-COLLECTIONS.md` du dépôt GeoMys.
2. **GeoNature** : rien à modifier pour démarrer. Les champs standard ci-dessus suffisent, et l'export Synthèse ou Occtax permet de retrouver les spécimens (filtre sur `PREUVE_EXIST` = non numérique).
3. **Plus tard, si nécessaire**, un module `gn_module_collections`. Cela concerne les cas de prêts, de rangement (boîte, tiroir), de plusieurs spécimens par occurrence, de déterminations successives, de collections externes ou d'herbiers anciens. Voir les sections suivantes. Le module est à proposer et à discuter avec la communauté GeoNature (PnX-SI) avant de développer.

## Architecture d'un module Collections

### Rattachement par UUID, pas par module

Le module ne se greffe pas séparément sur Occtax, Monitoring ou Import. Il s'accroche à l'**UUID SINP de l'observation**, comme le font déjà `gn_commons.t_medias` et `gn_commons.t_validations` (colonnes `uuid_attached_row` + `id_table_location`, voir `backend/geonature/core/gn_commons/models/base.py`).

| Source de l'observation | UUID de rattachement |
|---|---|
| Occtax / GeoMys | `unique_id_sinp_occtax` (dénombrement) ou `unique_id_occurence_occtax` |
| Monitoring | UUID de l'observation monitoring |
| Import (collections reçues, données historiques) | `unique_id_sinp` de la Synthèse |

Toutes ces observations remontent dans la Synthèse avec leur UUID. Le module peut donc les lister et les filtrer au même endroit, sans dépendre d'un module de saisie particulier et sans modifier Occtax ou Monitoring.

### Ce que les champs Occtax standard ne savent pas représenter

1. **Plusieurs spécimens pour une même observation** (5 carabes d'un même piège).
2. **Un lot ou un tube qui contient plusieurs espèces** (tri d'un piège Barber). C'est le cas inverse du précédent : il faut une entité *lot/contenant*.
3. **Les déterminations successives** : terrain, puis labo, puis spécialiste ; étiquettes de révision sur une planche d'herbier.
4. **Le rangement et les mouvements** : collection, meuble, boîte, tiroir, prêts.
5. **Les étiquettes** : génération (export pour le publipostage) et lecture (photos).

### Tables envisagées (schéma `gn_collections`)

- **`t_lots`** : contenant (boîte, tube, piège trié). Code, type de préparation, rangement, lien optionnel vers une station ou un relevé.
- **`t_specimens`** :
  - `uuid_specimen`, `catalog_number`, imprimés sur l'étiquette ;
  - `id_lot` ;
  - `uuid_attached_row` + `id_table_location`, pour le lien avec l'observation (vide tant que le spécimen n'est pas envoyé dans la Synthèse) ;
  - champs « collection » et « texte exact de l'étiquette » (voir ci-dessous) ;
  - statut de traitement : transcrit → interprété → validé → envoyé dans la Synthèse.
- **`t_determinations`** : historique des identifications, avec le nom exact, le rattachement Taxref, le déterminateur, la date, la méthode et la mention de la détermination retenue.
- **`t_movements`** (plus tard) : prêts et transferts.
- Photos d'étiquettes et de spécimens : `gn_commons.t_medias`, sans nouvelle table.
- Permissions : système GeoNature habituel, code module `COLLECTIONS`.
- Même structure technique que `gn_module_individuals` (Flask blueprint, branche Alembic, frontend Angular).

### Deux façons de faire entrer des spécimens

- **On part du terrain** (GeoMys, Occtax) : l'observation existe déjà. On coche « spécimen collecté », le spécimen est créé avec l'UUID déjà imprimé sur l'étiquette.
- **On part d'une collection reçue ou d'un herbier ancien** : il n'y a pas d'observation. On transcrit l'étiquette, on l'interprète, puis le module crée l'observation via l'API Occtax ou le module Import, et y rattache le spécimen. Le module n'écrit pas lui-même dans la Synthèse, pour garder une seule source par donnée.

## Champs à ajouter (inspirés de Darwin Core)

On garde ce qui est écrit sur l'étiquette tel quel, et on stocke à côté l'interprétation normalisée, sans jamais remplacer l'un par l'autre. Les noms des champs reprennent le **Darwin Core** (standard des collections : GBIF, muséums, MNHN), pour faciliter les échanges.

**Identité du spécimen et de la collection**
- `institution_code`, `collection_code`, `catalog_number` (n° de part d'herbier ou de spécimen)
- `preparation` (nomenclature) : part d'herbier, épinglé, alcool, lame, graines, ADN…
- `type_status` : holotype, isotype, etc.
- `disposition` : en collection, prêté, perdu, détruit
- emplacement : meuble, boîte, tiroir

**Texte exact de l'étiquette**
- `verbatim_locality` : « Bois de Foix, au-dessus du moulin »
- `verbatim_date` : « 12.VI.1934 », « été 1890 »
- `verbatim_coordinates`, `verbatim_elevation`
- `recorded_by` : collecteur (« leg. »), plusieurs noms possibles
- `record_number` : n° de récolte du collecteur (très utilisé en herbier)
- `label_text` : transcription complète, ou texte brut issu de la lecture de photo

**Fiabilité de l'interprétation**
- incertitude de la localisation (m), méthode de géoréférencement (étiquette, carte, centroïde de commune)
- date imprécise : intervalle `date_min` / `date_max`, déjà prévu par la Synthèse

Ce qui existe déjà dans la Synthèse et reste utilisé tel quel : `nom_cite`, `cd_nom`, `date_min` / `date_max`, `determiner`, `sample_number_proof`, `non_digital_proof`, `precision`. Il vaut mieux **ne pas** tout mettre dans les champs additionnels d'Occtax : pas de recherche efficace, pas d'historique de déterminations, pas de lien multiple spécimens ↔ observation.

## Noms absents de Taxref

### Ce que fait GeoNature aujourd'hui sans `cd_nom`

- **Synthèse** : `cd_nom` peut être vide (`backend/geonature/migrations/data/core/synthese.sql`). Mais les vues `v_synthese_for_web_app` et `v_synthese_for_export` font un `JOIN taxonomie.taxref` qui exclut ces lignes (migrations `446e902a14e7`, `9f4db1786c22`). La donnée n'apparaît **ni sur la carte ni dans les exports**.
- **Import** : `check_cd_nom` (`backend/geonature/core/imports/checks/sql/extra.py`) **rejette** les lignes dont le `cd_nom` n'existe pas dans Taxref.
- **Sensibilité, profils, listes de taxons, Monitoring, Individuals** : tout fonctionne par `cd_nom` / `cd_ref`, souvent avec la contrainte `NOT NULL`.

En pratique, une donnée sans `cd_nom` ne peut pas vivre dans la Synthèse.

### Démarche, dans l'ordre

1. **Chercher parmi les synonymes.** Taxref contient beaucoup de noms anciens et invalides, rattachés au même `cd_ref`. Recherche approchée sur `lb_nom` / `nom_complet`, avec et sans auteur.
2. **Sinon, se rattacher au rang supérieur** (pratique SINP) : `cd_nom` du genre, ou de l'espèce pour une variété absente, et nom exact dans `nom_cite`.
3. **Demander l'ajout à Taxref** (MNHN, via l'INPN ; mise à jour annuelle). Une fois le nom ajouté, on remplace le `cd_nom` du rang supérieur par le vrai ; l'historique reste dans `t_determinations`.
4. **Garder hors Synthèse ce qui n'a aucun équivalent** : spécimens étrangers (Taxref ne couvre que la France), cultivars, hybrides horticoles. Ils restent dans le module Collections, avec un lien éventuel vers un référentiel mondial (**WFO** pour les plantes, ossature taxonomique GBIF).

**À éviter :** créer des « taxons locaux » dans `taxonomie.taxref` avec des `cd_nom` inventés. Ils seraient cassés ou en conflit à la mise à jour annuelle de Taxref (via TaxHub) et inconnus de l'INPN lors des échanges SINP.

### Champs correspondants dans le module (spécimen ou détermination)

| Champ | Rôle |
|---|---|
| `nom_verbatim` | ce qui est écrit sur l'étiquette, toujours rempli |
| `cd_nom` (facultatif) | taxon Taxref trouvé : exact, synonyme ou rang supérieur |
| `statut_rattachement` (nomenclature) | exact / synonyme / rang supérieur / absent de Taxref / à revoir |
| `ref_externe` + `id_externe` (facultatifs) | par exemple `WFO` + `wfo-0000…` |
| `demande_taxref` (facultatif) | date ou n° de la demande d'ajout au MNHN |

Envoi vers la Synthèse :
- exact / synonyme / rang supérieur : l'observation est créée avec `cd_nom` et `nom_cite` = `nom_verbatim` ;
- absent de Taxref sans rang supérieur utilisable : le spécimen reste « en attente » dans le module, dans une liste de travail pour un spécialiste, et n'est pas envoyé.

## Questions ouvertes

- `non_digital_proof` (texte libre) suffit-il, ou faut-il un champ additionnel structuré pour le n° de spécimen ?
- Collections reçues d'autres structures : dans quel jeu de données les rattacher, et avec quel producteur ?
