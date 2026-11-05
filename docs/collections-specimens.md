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
3. **Plus tard, si nécessaire**, un module `gn_module_collections`. Cela concerne les cas de prêts, de rangement (boîte, tiroir), de plusieurs spécimens par occurrence, de déterminations successives ou de collections externes. Esquisse :
   - schéma `gn_collections` ;
   - `t_specimens` : `id_specimen`, `uuid_specimen`, `unique_id_occurence_occtax` (ou `id_synthese`), code de spécimen, type de conservation (nomenclature à créer : épinglé, alcool, lame, sec…), lieu de stockage, collection d'appartenance, commentaire ;
   - `t_specimen_determinations` : déterminations successives (cd_nom, déterminateur, date, méthode) ;
   - `t_loans` : prêts (emprunteur, dates) ;
   - même structure de module que `gn_module_individuals` (Flask blueprint, Alembic branch, frontend Angular) ;
   - à proposer et discuter avec la communauté GeoNature (PnX-SI) avant de développer, éventuellement sous forme d'extension de `individuals` vers un objet « spécimen ».

## Questions ouvertes

- `non_digital_proof` (texte libre) suffit-il, ou faut-il un champ additionnel structuré pour le n° de spécimen ?
- Collections reçues d'autres structures : dans quel jeu de données les rattacher, et avec quel producteur ?
