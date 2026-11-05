# Spécimens en collection : analyse et pistes

Note faite le 2026-11-05 à la suite de l'issue GeoMys [ANA-CEN-Ariege/GeoMys-android#54](https://github.com/ANA-CEN-Ariege/GeoMys-android/issues/54) « Module de gestion/saisie des spécimens en collection ». Elle a été révisée après une relecture critique (code GeoNature, issue, standards des collections).

Les chemins sont relatifs à la racine du dépôt GeoNature (fork 2.17.x).

## Le besoin

Il porte sur les invertébrés conservés en collection (épinglés ou en alcool) et sur les herbiers.

- **Saisir des données à partir des étiquettes** des spécimens, y compris des collections reçues pour étude et des herbiers anciens. Il faut pouvoir le faire en grande quantité, au bureau, à partir de photos.
  - Les étiquettes « station » (commune, lieu-dit, GPS, date, `leg.`) et « identité » (taxon, `det.`) sont souvent séparées et empilées sur la même épingle.
  - Les noms peuvent être absents de Taxref.
- **Produire des étiquettes** à partir des données saisies : export tableur → publipostage Word, avec un identifiant qui relie le spécimen à la donnée naturaliste.
- La saisie doit être possible **en web** (GeoNature) et sur le terrain (GeoMys).
- La collecte peut venir d'une **observation opportuniste comme d'un suivi**.

## `gn_module_individuals` ne convient pas

[PnX-SI/gn_module_individuals](https://github.com/PnX-SI/gn_module_individuals) : branches `master` (0.0.1) et `develop`, lues en novembre 2026.

- **Objet** : le suivi d'animaux **vivants** identifiés individuellement (capture-marquage-recapture, balises GPS/GSM/VHF, marques couleur).
- **Tables du module** :
  - `gn_individual.t_tracking_devices` : type de dispositif (`TYPE_DISPO_SUIVI`), fournisseur, code ;
  - `gn_individual.t_individual_deployments` : pose d'un dispositif ou d'une marque (`LOC_DISPO_SUIVI`), dates de pose et de retrait.
- **Tables du cœur GeoNature**, réutilisées par le module : `gn_monitoring.t_individuals` et `t_marking_events` (`backend/geonature/core/gn_monitoring/models.py`, migration `84f40d008640`).
- **Compatibilité** : la branche `develop` utilise des colonnes `id_individual` dans `cor_counting_occtax` et dans la Synthèse, ajoutées dans GeoNature amont. Elles **n'existent pas dans ce fork**. Le module demande GeoNature ≥ 2.17 amont.
- Il n'y a pas de notion de spécimen, de lot, de lieu de conservation, d'étiquette, de numéro de catalogue, ni d'export d'étiquettes.

On le garde pour le suivi d'individus vivants et on ne le détourne pas pour les collections. Deux éléments sont tout de même à réutiliser :

- `routes/observations.py` liste les observations de la Synthèse avec permissions, floutage et pagination (`SyntheseQuery`). C'est le modèle direct de la **liste de travail** du module Collections.
- **Précédent de rattachement** : l'équipe GeoNature amont relie l'individu à l'observation par une **colonne directe** (`id_individual` dans le dénombrement et dans la Synthèse), pas par `uuid_attached_row`. Voir la discussion plus bas.

## Ce qu'Occtax et la Synthèse permettent déjà

### Champs de preuve

| Champ (occurrence Occtax → Synthèse) | Usage pour une collection |
|---|---|
| `id_nomenclature_exist_proof` (`PREUVE_EXIST`) | **« Oui » (code `1`)** = une preuve existe. La nomenclature dit *s'il y a* une preuve, pas de quel type. |
| `non_digital_proof` | preuve physique : institution, collection, lieu de conservation, code du spécimen si on saisit dans Occtax web |
| `sample_number_proof` | **n° du spécimen ou de l'échantillon**. L'API Occtax l'accepte, mais le formulaire web ne l'affiche pas : on le remplit depuis GeoMys ou le module. |
| `digital_proof` | URL d'une photo du spécimen ou de l'étiquette |
| `determiner`, `id_nomenclature_determination_method` | déterminateur et méthode (examen des genitalia, etc.) |
| `nom_cite`, `cd_nom` | nom tel que cité, et rattachement Taxref |
| `additional_fields` | champ structuré supplémentaire si besoin, voir `occtax-additional-fields.md` |

Quand `PREUVE_EXIST` vaut `1`, le formulaire Occtax exige une preuve numérique ou non numérique (`contrib/occtax/frontend/app/occtax-form/occurrence/occurrence.service.ts:164`).

**Critère « spécimen collecté »** : `PREUVE_EXIST = 1` ET (`non_digital_proof` ou `sample_number_proof` renseigné).

### Identifiants (UUID)

- `synthese.unique_id_sinp` = **UUID du dénombrement** Occtax (`unique_id_sinp_occtax`). C'est le déclencheur qui le recopie (`contrib/occtax/backend/occtax/migrations/data/occtax.sql:189`), et il est unique dans la Synthèse.
- `synthese.unique_id_sinp_grp` = UUID du relevé, c'est-à-dire la station.
- `unique_id_occurence_occtax` (occurrence) **n'est pas recopié dans la Synthèse** et n'a pas de contrainte d'unicité. Il ne sert qu'à l'intérieur d'Occtax.

**Conséquence pour imprimer une étiquette avant l'envoi** : l'application doit **générer elle-même `unique_id_sinp_occtax`** et l'envoyer. L'API l'accepte, car le champ n'est pas en lecture seule dans `CountingSchema` (`contrib/occtax/backend/occtax/schemas.py`). Aujourd'hui GeoMys ne l'envoie pas : c'est le serveur qui le crée.

Un dénombrement peut regrouper plusieurs spécimens. Son UUID identifie la **donnée**, pas chaque spécimen. Chaque spécimen a donc besoin de son **propre identifiant**, voir plus bas.

## `gn_module_individuals` est exclu ; on crée un module Collections transversal

La collecte d'un spécimen ne dépend pas du type de saisie : observation opportuniste (Occtax), protocole de suivi (Monitoring), import, herbier ancien. Le module Collections est donc **indépendant**, avec sa propre saisie web.

Un sous-module Monitoring a été écarté pour trois raisons :
- il réserverait la collecte aux suivis ;
- il imposerait un site ;
- il ne permet ni l'historique des déterminations ni les lots mélangés.

**Occtax et Monitoring ne sont pas modifiés.** Le lien passe uniquement par les champs standard et les UUID.

### Comment rattacher un spécimen à une observation

Deux modèles existent dans GeoNature :

| Modèle | Exemples | Avantages | Inconvénients |
|---|---|---|---|
| **UUID générique** : `uuid_attached_row` + `id_table_location` | `gn_commons.t_medias`, `gn_commons.t_history_actions` (`backend/geonature/core/gn_commons/models/base.py`) | marche pour toutes les sources, aucune modification des modules existants | pas de clé étrangère, donc pas d'intégrité garantie par la base |
| **UUID → Synthèse avec clé étrangère** | `gn_commons.t_validations` : `uuid_attached_row` est une clé étrangère vers `synthese.unique_id_sinp` (pas d'`id_table_location`) | intégrité garantie, rattachement à la donnée « consolidée » | le spécimen ne peut être rattaché qu'à une donnée déjà dans la Synthèse |
| **Colonne directe** | `id_individual` dans le dénombrement et la Synthèse (GeoNature amont, module Individuals) | jointure simple, affichable dans la Synthèse | modifie Occtax et la Synthèse, à négocier en amont |

**Recommandation** : rattacher à `synthese.unique_id_sinp` (comme `t_validations`), en gardant le champ vide tant que le spécimen n'est pas encore relié à une observation. On peut ensuite proposer à PnX-SI une colonne directe, sur le modèle de `id_individual`, si l'affichage des spécimens dans la Synthèse devient nécessaire.

### Comment un spécimen entre dans le module

1. **Observation opportuniste** (Occtax web ou GeoMys).
   - On renseigne `PREUVE_EXIST` = Oui et le code du spécimen (`non_digital_proof` dans Occtax web, `sample_number_proof` depuis GeoMys).
   - L'observation apparaît dans la **liste de travail** du module : observations de la Synthèse qui remplissent le critère « spécimen collecté » et n'ont pas de fiche spécimen.
   - Un clic ouvre la fiche spécimen déjà rattachée. On complète la préparation, le rangement et les déterminations, puis on imprime l'étiquette.
2. **Protocole de suivi** (Monitoring web ou GeoMys). Même mécanisme, à vérifier pour chaque protocole :
   - les observations Monitoring ont un `uuid_observation` (`backend/geonature/core/gn_monitoring/models.py:260`), qui doit être repris en `unique_id_sinp` dans la vue Synthèse du protocole ;
   - elles n'ont pas de champ « preuve » standard : il faut l'ajouter dans la configuration du protocole et le transmettre à la Synthèse (`id_nomenclature_exist_proof`, `non_digital_proof`, `sample_number_proof`).
3. **Spécimen sans observation** (herbier ancien, collection reçue). Saisie complète dans le module.
   - Texte de l'étiquette, puis interprétation (lieu, date, nom avec rattachement Taxref), puis validation.
   - L'observation est créée dans un **Occtax dupliqué `COLLECTIONS`** (`docs/admin-manual.rst`, « Dupliquer le module Occtax »). Il a :
     - sa propre source dans la Synthèse ;
     - ses jeux de données (`cor_module_dataset`), par exemple « Herbier ANA – fonds X » ;
     - ses champs additionnels et ses permissions.
   - Le module appelle les routes `/occtax/COLLECTIONS/only/releve` puis `/occtax/COLLECTIONS/releve/<id>/occurrence`. La route `POST /releve` est dépréciée (`contrib/occtax/backend/occtax/blueprint.py:216`).
   - Il transmet `unique_id_sinp_occtax` pour connaître l'UUID à l'avance.
   - Pour un gros lot, on passe par le module Import.
   - Si le nom n'a aucun équivalent dans Taxref, la fiche reste « en attente » dans le module, sans observation.
4. **Rattachement après coup** : depuis une fiche spécimen, on cherche une observation dans la Synthèse (date, observateur, taxon, lieu) pour la relier.

Le module n'écrit jamais lui-même dans la Synthèse, pour garder une seule source par donnée.

### Synchronisation des déterminations

- La **détermination retenue** du spécimen est la seule qui met à jour l'observation source : `cd_nom`, `nom_cite`, `determiner` de l'occurrence Occtax. Les autres révisions restent dans l'historique du module.
- Le module fait cette mise à jour seulement s'il en a le droit sur la source, c'est-à-dire sur l'Occtax `COLLECTIONS`. Pour une observation d'un autre module, il **propose** la correction au propriétaire de la donnée, sur le modèle de la validation.
- **N spécimens d'un même dénombrement avec des déterminations différentes** : on découpe. Un nouveau dénombrement ou une nouvelle occurrence est créé pour chaque taxon, avec son propre UUID, et chaque spécimen est rattaché au bon dénombrement.
- **Rattachement au rang supérieur** (genre) : la Synthèse affichera le genre. La sensibilité, les filtres et la validation se feront aussi au niveau du genre. Il faut le dire aux utilisateurs.

### Identifiant de chaque spécimen

- `uuid_specimen`, exposé comme **URI HTTP résolvable**, par exemple `https://<instance>/collections/specimen/<uuid>` (modèle des identifiants pérennes CETAF, *CETAF Stable Identifiers*).
- Le **QR code** de l'étiquette contient cette URI. Scanner l'étiquette ouvre la fiche, ou rattache directement le spécimen dans GeoMys.
- Pour un herbier : un **code-barres** est collé sur la planche avant sa numérisation, et il est repris en `catalogNumber`.

## Champs du module (alignés sur Darwin Core)

On garde ce qui est écrit sur l'étiquette tel quel, et on stocke à côté l'interprétation normalisée, sans jamais remplacer l'un par l'autre. Les noms de champs reprennent le **Darwin Core** (version 2023), y compris la classe `MaterialEntity`, et l'extension GBIF **Identification History**.

**Identité de l'objet et de la collection**
- `materialEntityID` (= `uuid_specimen`), `basisOfRecord = PreservedSpecimen`
- `institutionCode` et `collectionCode` : codes **Index Herbariorum** pour les herbiers, **GRSciColl** pour les autres
- `catalogNumber` (n° de part ou de spécimen), `otherCatalogNumbers` (anciens numéros, numéro chez une autre institution)
- `preparations` (nomenclature) : part d'herbier, épinglé, alcool, lame, graines, ADN…
- `typeStatus` (holotype, isotype…), lié au `typifiedName`
- `disposition` : en collection, prêté, perdu, détruit
- état et conservation (dégâts, date de contrôle), emplacement (meuble, boîte, tiroir)

**Texte exact de l'étiquette**
- `verbatimLabel` : transcription complète de toutes les étiquettes, ou texte brut issu de la lecture de photo
- `verbatimLocality`, `verbatimEventDate`, `verbatimCoordinates`, `verbatimElevation`
- `recordedBy` (collecteur, `leg.`, plusieurs noms possibles), `recordNumber` (n° de récolte du collecteur, très utilisé en herbier)

**Déterminations** (`t_determinations`, alignée sur Identification History)
- `verbatimIdentification` (nom tel qu'écrit), `identificationQualifier` (cf., aff., ?)
- `cd_nom` et statut de rattachement Taxref (voir plus bas)
- `identifiedBy`, `dateIdentified`, méthode, `identificationVerificationStatus`, `identificationRemarks`
- indicateur de la détermination retenue

**Fiabilité de l'interprétation**
- incertitude de la localisation (m) et méthode de géoréférencement (étiquette, carte, centroïde de commune)
- date imprécise : intervalle `date_min` / `date_max`, déjà prévu par la Synthèse

Il vaut mieux **ne pas** tout mettre dans les champs additionnels d'Occtax : on ne pourrait ni chercher efficacement, ni garder l'historique des déterminations, ni relier plusieurs spécimens à une même donnée.

## Noms absents de Taxref

### Ce que fait GeoNature aujourd'hui

- **Synthèse**
  - `cd_nom` peut être vide, mais c'est une clé étrangère vers `taxonomie.taxref` (`backend/geonature/migrations/data/core/synthese.sql:248`). `nom_cite` est NOT NULL.
  - La vue `v_synthese_for_web_app` (définition actuelle : migration `cb663f039774`) fait un `JOIN taxonomie.taxref`. L'export par défaut repose sur des vues construites de la même façon.
  - Une ligne sans `cd_nom` n'apparaît donc **ni dans la liste ni sur la carte ni dans l'export par défaut**. Sa fiche reste lisible si on connaît son identifiant, car elle lit directement la table.
- **Import** : `cd_nom` est obligatoire et `check_cd_nom` (`backend/geonature/core/imports/checks/sql/extra.py`) rejette les `cd_nom` absents de Taxref. La liste restrictive `ID_LIST_TAXA_RESTRICTION`, si elle est configurée, rejette aussi des noms pourtant présents dans Taxref.
- **Monitoring** : `cd_nom` est NOT NULL (`backend/geonature/core/gn_monitoring/models.py:258`).
- **Sensibilité, profils, listes de taxons** : tout fonctionne par `cd_nom` / `cd_ref`.

En pratique, une donnée sans `cd_nom` ne peut pas vivre dans la Synthèse.

### Démarche, dans l'ordre

1. **Chercher parmi les synonymes.** Taxref contient beaucoup de noms anciens et invalides, rattachés au même `cd_ref`. Recherche approchée sur `lb_nom` / `nom_complet`, avec et sans auteur.
2. **Sinon, se rattacher au rang supérieur** (pratique SINP) : `cd_nom` du genre, ou de l'espèce pour une variété absente. Le nom exact va dans `nom_cite` et `verbatimIdentification`.
3. **Demander l'ajout à Taxref** (MNHN, via l'INPN ; mise à jour annuelle). Une fois le nom ajouté, une nouvelle détermination remplace le rattachement au rang supérieur.
4. **Garder hors Synthèse ce qui n'a aucun équivalent** : spécimens étrangers (Taxref ne couvre que la France), cultivars, hybrides horticoles. Ils restent dans le module Collections, avec un lien éventuel vers un référentiel mondial (**WFO** pour les plantes, ossature taxonomique GBIF).

**À éviter :** créer des « taxons locaux » dans `taxonomie.taxref` avec des `cd_nom` inventés. Ils seraient cassés ou en conflit à la mise à jour annuelle de Taxref (via TaxHub) et inconnus de l'INPN lors des échanges SINP.

### Champs correspondants (sur chaque détermination)

| Champ | Rôle |
|---|---|
| `verbatimIdentification` | nom tel qu'écrit sur l'étiquette, toujours rempli |
| `cd_nom` (facultatif) | taxon Taxref trouvé : exact, synonyme ou rang supérieur |
| `statut_rattachement` (nomenclature) | exact / synonyme / rang supérieur / absent de Taxref / à revoir |
| `ref_externe` + `id_externe` (facultatifs) | par exemple `WFO` + `wfo-0000…` |
| `demande_taxref` (facultatif) | date ou n° de la demande d'ajout au MNHN |

Envoi vers la Synthèse :
- **exact, synonyme ou rang supérieur** : l'observation est créée avec `cd_nom` et `nom_cite` = nom exact ;
- **absent de Taxref, sans rang supérieur utilisable** : le spécimen reste « en attente » dans le module, dans une liste de travail pour un spécialiste.

## Étiquettes

### Types d'étiquettes

| Type | Contenu | Granularité |
|---|---|---|
| Station | pays/département, commune, lieu-dit, WGS84, altitude, date, `leg.` | relevé |
| Station + taxon | + nom, `det.` | dénombrement |
| Station + taxon + dénombrement | + stade, sexe, effectif | dénombrement |
| **Détermination** (`det.`) | nom, qualificatif, déterminateur, année | détermination |
| **Herbier** | grand format : en-tête de la collection, nom, lieu, habitat, date, `leg.` + n° de récolte, `det.`, code-barres | spécimen |

Pour chaque type :
- l'export est un CSV UTF-8 avec BOM et séparateur `;` (publipostage Word) ;
- le fichier contient une colonne avec l'URI du spécimen pour le QR code, sinon l'UUID de la donnée ;
- mise en page des étiquettes entomologiques : petit corps (4 à 5 pt), une information par ligne, coordonnées WGS84 décimales.

**En attendant le module**, pour les données saisies dans Occtax web : l'export Occtax permet déjà un premier publipostage. Il faut l'étendre pour qu'il contienne l'UUID du dénombrement, si ce n'est pas déjà le cas.

## Saisie en masse au bureau

Le volet « saisie depuis les étiquettes » de l'issue concerne surtout le travail **au bureau**, sur des boîtes entières.

- Prendre une **série de photos** (ou scanner) : une photo par spécimen, toutes les étiquettes de l'épingle étalées, ou une planche d'herbier entière.
- **Lecture des photos côté serveur**, en complément de ML Kit sur le téléphone, qui reconnaît mal le manuscrit. Les photos alimentent une **file de transcription** : « photographier d'abord, transcrire ensuite », comme Les Herbonautes et Symbiota. On peut prévoir une double saisie ou un consensus pour les étiquettes anciennes.
- **Associer** l'étiquette station et l'étiquette identité d'un même spécimen. Une étiquette station déjà transcrite est réutilisée pour tous les spécimens qui la portent.
- La fiche suit les statuts transcrit → interprété → validé → envoyé dans la Synthèse.

## Compléments propres aux collections

À prévoir dans le modèle, sans forcément les développer dès la première version :

- **Relations entre objets** (sur le modèle de *Resource Relationship* de Darwin Core) : part de, duplicata envoyé à une autre institution, dérivé de (sous-échantillon ADN, lame de genitalia), etc. Pour l'ADN, extension GGBN MaterialSample.
- **Lots mixtes** : un lot (tube, tri de piège Barber) **ne porte pas de taxon**. Il contient N unités taxon × effectif (`individualCount`), qu'on peut détacher plus tard en spécimens individuels.
- **Entrées en collection** : don, échange, dépôt, matériel reçu pour étude, avec date et origine. C'est la réponse à la question des collections reçues d'autres structures.
- **Permis** : autorisations de capture d'espèces protégées et APA/Nagoya (loi Biodiversité 2016), liés à la récolte ou à l'entrée.
- **Description des fonds** avec Latimer Core (TDWG), qui décrit une collection et non un spécimen.
- **Exports** :
  - Darwin Core Archive (spécimens + extension Identification History) pour IPT/GBIF ;
  - export vers **e-ReColNat**, le portail national des collections. Le format accepté est à vérifier avec Recolnat.

## Découpage en phases

1. **Phase 1, sans nouveau module** :
   - export d'étiquettes depuis GeoMys (station, station + taxon, station + taxon + dénombrement) ;
   - GeoMys génère et envoie `unique_id_sinp_occtax` ;
   - case « spécimen collecté » (`PREUVE_EXIST` = Oui + `sample_number_proof`) ;
   - solution d'attente par l'export Occtax pour les données saisies en web.
2. **Phase 2, module `gn_module_collections`** (même structure technique que `gn_module_individuals` : blueprint Flask, branche Alembic, frontend Angular) :
   - spécimens, déterminations, rattachement à la Synthèse, liste de travail ;
   - saisie web, saisie en masse et file de transcription ;
   - Occtax dupliqué `COLLECTIONS` pour créer les observations ;
   - étiquettes de détermination et d'herbier ;
   - URI et QR code par spécimen.
3. **Phase 3** :
   - lots mixtes, mouvements et prêts, relations entre objets, entrées en collection, permis ;
   - exports DwC-A et e-ReColNat ;
   - lecture des photos côté serveur, si la phase 2 s'est limitée à la saisie manuelle.

Le module est à proposer et à discuter avec la communauté GeoNature (PnX-SI) avant de développer. D'autres structures ont sans doute le même besoin.

## Questions ouvertes

- Rattachement : UUID vers la Synthèse (sans modifier le cœur) ou colonne directe façon `id_individual` (à négocier en amont) ?
- Collections reçues d'autres structures : quel jeu de données, quel producteur, quelle entrée en collection ?
- Format d'échange exact attendu par e-ReColNat.
- Faut-il afficher le formulaire `sample_number_proof` dans Occtax web (configuration ou contribution amont) ?
