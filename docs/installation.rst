Installation
============

GeoNature repose sur les composants suivants :

- PostgreSQL / PostGIS
- Python 3 et dépendances Python nécessaires à l'application
- Flask (framework web Python)
- Apache
- Angular 15, Angular CLI, NodeJS
- Librairies javascript (Leaflet, ChartJS)
- Librairies CSS (Bootstrap, Material Design)


Trois méthodes d’installation existent :

- :ref:`installation-all` : Installation automatisée de GeoNature (incluant TaxHub) et UsersHub.
- :ref:`installation-standalone` : UsersHub n'est pas installé (mais son schéma de BDD est tout de même créé dans la base de données de GeoNature).
- :ref:`installation-docker` : GeoNature et ses principaux modules (Export, Dashboard, Monitoring) ainsi que UsersHub installés avec Docker (solution la plus simple recommandée).


Prérequis
*********

- Ressources minimum serveur :

  - Un serveur Debian 11, 12 ou 13, architecture 64-bits
  - 4 Go RAM (6 Go conseillé)
  - 20 Go d’espace disque


- GeoNature nécessite d’accéder à des ressources externes durant son installation et son fonctionnement. Si vous utilisez un serveur mandataire, celui-ci doit permettre l’accès aux domaines suivants :

  - https://pypi.python.org
  - https://geonature.fr/
  - https://codeload.github.com/
  - https://nodejs.org/dist
  - https://registry.npmjs.org
  - https://www.npmjs.com
  - https://raw.githubusercontent.com/
  - https://data.geopf.fr/


.. _preparation-server:

Préparation du serveur
**********************

Commencer la procédure en se connectant au serveur en SSH avec l'utilisateur linux ``root``.

* Mettre à jour de la liste des dépôts Linux :

  .. code:: shell

    apt update
    apt upgrade

* Configuration de la locale du serveur

  Certains serveurs sont livrés sans "locale" (langue par défaut). Pour l'installation de GeoNature, il est nécessaire de bien configurer la locale. Si la commande ``locale`` renvoie ceci :

  .. code:: console

    LANG=fr_FR.UTF-8
    LANGUAGE=fr_FR.UTF-8
    LC_CTYPE="fr_FR.UTF-8"
    LC_NUMERIC="fr_FR.UTF-8"
    LC_TIME="fr_FR.UTF-8"
    LC_COLLATE="fr_FR.UTF-8"
    LC_MONETARY="fr_FR.UTF-8"
    LC_MESSAGES="fr_FR.UTF-8"
    LC_PAPER="fr_FR.UTF-8"
    LC_NAME="fr_FR.UTF-8"
    LC_ADDRESS="fr_FR.UTF-8"
    LC_TELEPHONE="fr_FR.UTF-8"
    LC_MEASUREMENT="fr_FR.UTF-8"
    LC_IDENTIFICATION="fr_FR.UTF-8"
    LC_ALL=fr_FR.UTF-8

  Vous pouvez alors passer cette étape de configuration des locales.

  Sinon exécuter la commande ``dpkg-reconfigure locales``. Une fenêtre s'affiche dans votre console. Dans la liste déroulante, sélectionnez ``fr_FR.UTF-8 UTF-8`` avec ``Espace``, puis cliquez sur OK. Une 2ème fenêtre s'affiche avec une liste de locale activées (``fr_FR.UTF-8`` doit être présent dans la liste), confirmez votre choix, en cliquant sur OK, puis attendez que la locale s'installe.


* Installer l’utilitaire ``sudo`` :

  .. code:: shell

    apt install sudo

* Créer un utilisateur Linux dédié (nommé ``geonatureadmin`` dans notre cas) pour ne pas travailler en ``root`` :

  .. code:: shell

    adduser geonatureadmin

* Lui donner ensuite les droits administrateur en l’ajoutant au groupe ``sudo`` :

  .. code:: shell

    adduser geonatureadmin sudo

.. note::
        Vérifier les droits du répertoire HOME de l'utilisateur
          
        Il doit être en ``7XX`` soit ``drwxr-xr-x``. Si ce n'est pas le cas, exécuter la commande suivante :
        
        .. code:: shell
      
          chmod 755 /home/geonatureadmin
* Pour la suite du processus d’installation, on utilisera l'utilisateur non privilégié nouvellement créé. Si besoin d'éxecuter des commandes avec les droits d'administrateur, on les précèdera de ``sudo``.

  Il est d'ailleurs possible renforcer la sécurité du serveur en bloquant la connexion SSH au serveur avec ``root``. Voir https://docs.ovh.com/fr/vps/conseils-securisation-vps/ pour plus d'informations sur le sécurisation du serveur.
  
  Pour passer de l’utilisateur ``root`` à ``geonatureadmin``, vous pouvez aussi utiliser la commande :

  .. code:: shell

    su - geonatureadmin


.. _installation-all:

.. include:: installation-all.rst

.. _installation-standalone:

.. include:: installation-standalone.rst

.. include:: installation-docker.rst

.. include:: https.rst

.. _cron:

Taches planifiées
*****************

Depuis sa version 2.12.0, les taches planifiées gérées par des crons ont été remplacées par des taches lancées automatiquement par Celery Beat, pour être mis en place automatiquement lors de l'installation de GeoNature et être plus facilement administrables.


.. _install-gn-module:

Installation d'un module GeoNature
**********************************

L'installation de GeoNature n'est livrée qu'avec les modules du coeur par défaut : Occtax, Occhab et Validation. Pour ajouter un module GeoNature externe, il est nécessaire de l'installer :

Téléchargement
--------------

Téléchargez le module depuis son dépôt Github puis dézippez-le dans le repertoire utilisateur, au même niveau que le dossier de GeoNature.

.. _install-gn-module-auto:

Installation automatique
------------------------

Installation avec la sous-commande ``install-gn-module`` :

.. code-block:: bash

    source <dossier GeoNature>/backend/venv/bin/activate
    geonature install-gn-module <dossier du module> <code du module>

Exemple pour le module Import :

.. code-block:: bash

    source ~/GeoNature/backend/venv/bin/activate
    geonature install-gn-module ~/gn_module_import/ IMPORT

Puis relancer GeoNature et son worker :

.. code-block:: bash

    sudo systemctl restart geonature
    sudo systemctl restart geonature-worker

Aucune permission n'est définie par défaut lors de l'installation d'un module. En tant qu'administrateur, vous pouvez exécuter une commande ajoutant tous les droits sur tous les modules à un groupe ou utilisateur. Cette commande peut être relancée après l'installation d'un module pour automatiquement attribuer toutes les permissions à un groupe ou utilisateur administrateur :

.. code-block:: bash

    # changer "Grp_Admin" par le nom de votre groupe d'administrateur si vous l'avez changé
    geonature permissions supergrant --group --nom "Grp_admin"

Vous devrez ensuite définir des droits à vos utilisateurs pour le nouveau module installé.

Installation manuelle
---------------------

**Installation du backend**

Installer le module avec ``pip`` en mode éditable après avoir activé le venv de GeoNature, puis relancer GeoNature :

.. code-block:: bash

    source ~/geonature/backend/venv/bin/activate
    pip install --editable <dossier du module>
    sudo systemctl restart geonature

.. _module_install_frontend:

**Installation du frontend**

* Créer un lien symbolique dans le dossier ``frontend/external_modules`` de GeoNature vers le dossier ``frontend`` du module.
  Le lien symbolique doit être nommé suivant le code du module en minuscule :

  .. code-block:: bash

      cd ~/geonature/frontend/external_modules/
      ln -s <dossier du module>/frontend <code du module en minuscule>

  Exemple pour le module Import :

  .. code-block:: bash

      cd ~/geonature/frontend/external_modules/
      ln -s ~/gn_module_import/frontend import

* Re-builder le frontend :

  .. code-block:: bash

      cd ~/geonature/frontend/
      nvm use
      npm run build

**Installation de la base de données**

.. code-block:: bash

    source ~/geonature/backend/venv/bin/activate
    geonature upgrade-modules-db <code du module>

.. _module-config:

Configuration du module
-----------------------

De manière facultative, vous pouvez modifier la configuration du module.
Pour cela, vous pouvez créer un fichier de configuration dans le dossier ``config`` de GeoNature en le nommant ``<code du module en minuscule>_config.toml``.
La plupart des modules fournissent un fichier d’exemple ``<module-code>_config.toml.example`` dans leur dossier racine dont vous pouvez vous inspirer.

Après création de ce fichier, activez le rechargement automatique de GeoNature lors des modifications du fichier en lançant les commandes suivantes :

.. code-block:: bash

    sudo systemctl daemon-reload
    sudo systemctl restart geonature

Avant la version 2.12.0 de GeoNature, après chaque modification de la configuration du module, vous devez recharger GeoNature manuellement :

.. code-block:: bash

    sudo systemctl reload geonature


Mise à jour du module
---------------------

* Déplacer le code de l’ancienne version du module : ``mv gn_module_xxx gn_module_xxx_old``
* Télécharger et désarchiver la nouvelle version du module, et renommer son dossier afin qu’il porte le même nom qu’avant (*e.g.* ``gn_module_xxx``)
* (Optionnel) Si le fichier de configuration du module est encore placé avec celui-ci, le déplacer dans la dossier de configuration centralisée de GeoNature : ``cp gn_module_xxx_old/config/conf_gn_module.toml geonature/config/<mode-code>_config.toml``
* Relancer l’:ref:`installation du module<install-gn-module-auto>` : ``geonature install-gn-module gn_module_xxx XXX && sudo systemctl reload geonature``


Mise à jour de l'application
****************************

.. warning::
    Avant chaque mise à jour de GeoNature, il est important de sauvegarder l'application et sa base de données, ou de faire un snapshot du serveur pour pouvoir revenir à son état antérieure avant mise à jour, en cas de problème.

.. warning::
    Vérifiez préalablement la compatibilité des modules que vous utilisez avant de mettre GeoNature à jour.
    S’il est nécessaire de les mettre à jour, arrêtez vous après le remplacement du dossier par le nouveau code source
    (et la récupération éventuelle de la configuration); le script de migration de GeoNature s’occupera automatiquement
    d’installer la nouvelle version du module.

La mise à jour de GeoNature consiste à télécharger sa nouvelle version dans un nouveau répertoire, récupérer les fichiers de configuration et de surcouche, ainsi que les médias depuis la version actuelle et de relancer l'installation dans le répertoire de la nouvelle version.

La mise à jour doit être réalisée avec votre utilisateur linux courant (``geonatureadmin`` par exemple) et non pas le super-utilisateur ``root``.

* Télécharger la dernière version de GeoNature :

  .. code-block:: bash

      wget https://github.com/PnX-SI/GeoNature/archive/X.Y.Z.zip
      unzip X.Y.Z.zip
      rm X.Y.Z.zip

* Renommer l'ancien repertoire de l'application, ainsi que le nouveau :

  .. code-block:: bash

      mv ~/geonature/ ~/geonature_old/
      mv ~/GeoNature-X.Y.Z ~/geonature/
      cd ~/geonature

* Suivez les éventuelles notes de version spécifiques décrites au niveau de chaque version : https://github.com/PnX-SI/GeoNature/releases.

Sauf mentions contraires dans les notes de version, vous pouvez sauter des versions mais en suivant bien les différentes notes de versions intermédiaires.

* Si vous devez aussi mettre à jour UsersHub, suivez ses notes de version et sa documentation (https://usershub.readthedocs.io).

* Lancez le script de ``migration.sh`` à la racine du dossier ``geonature``:

  .. code-block:: bash

      ./install/migration/migration.sh 2>&1 | tee install/migration/migration.log

Depuis la version 2.12, le script `migration.sh` peut prendre en argument le chemin vers l'ancien dossier d'installation de GeoNature. Il peut s’agir du même dossier que la nouvelle installation de GeoNature. Cela permet d'utiliser ce script si la nouvelle version de GeoNature est dans le même dossier et donc de gérer le cas où GeoNature est installé et mis à jour avec git.

Dans ce cas, les commandes à exécuter sont : 

.. code-block:: bash

    cd ~/GeoNature
    git status # optionnel, pour connaitre l'état et la version ou branche actuellement utilisée
    git fetch
    git checkout X.Y.Z # où X.Y.Z est le numéro du tag de la version vers laquelle faire la mise à jour, ou le nom de la branche à utiliser
    git submodule update # ou "git config submodule.recurse true" lancé une seule fois, pour que la mise à jour des sous-modules soit relancée automatiquement à chaque "git pull"
    ./install/migration/migration.sh .


.. _update-from-admin:

Mise à jour depuis l'interface d'administration
-----------------------------------------------

La page « Admin > Autres > Mises à jour » permet de lancer en un clic :

* la mise à jour de GeoNature vers la dernière version publiée sur GitHub ;
* la mise à jour d'un module externe vers un tag, une branche ou un commit ;
* la mise à jour du schéma de la base et des données de référence déjà installées
  (``geonature db autoupgrade`` et ``geonature upgrade-modules-db``) ;
* la régénération de la configuration et le rebuild du frontend (``geonature update-configuration``) ;
* des traitements sur les référentiels : cache des règles de sensibilité, sensibilité de la synthèse,
  profils de taxons ;
* la migration vers la version suivante de Taxref, en deux étapes (analyse, puis application).

La page affiche le journal de l'opération en direct. Les journaux sont conservés dans
``/var/log/geonature/update/`` et l'état de la dernière opération dans ``/var/lib/geonature/update/status.json``.

**Fonctionnement.** Le bouton envoie une tâche Celery qui valide la demande, l'écrit dans
``/var/lib/geonature/update/request.env`` et démarre le service systemd ``geonature-update.service``.
C'est ce service qui exécute ``install/update/geonature-update.sh``. Le worker Celery ne fait pas la mise à
jour lui-même, car il est arrêté pendant celle-ci. Le script peut aussi être lancé à la main :

.. code-block:: bash

    ./install/update/geonature-update.sh --dry-run core X.Y.Z   # affiche les commandes sans les exécuter
    ./install/update/geonature-update.sh core X.Y.Z
    ./install/update/geonature-update.sh module MONITORINGS 1.2.3
    ./install/update/geonature-update.sh db
    ./install/update/geonature-update.sh frontend
    ./install/update/geonature-update.sh ref sensitivity-cache
    ./install/update/geonature-update.sh taxref analyse:18      # puis : taxref apply:18

**Activation.**

1. Dans ``config/settings.ini``, passez ``install_updater=true``, puis relancez
   ``./install/02_configure_systemd.sh``. Ce script installe ``geonature-update.service`` et les droits sudo
   limités de ``/etc/sudoers.d/geonature-update`` : démarrage du service de mise à jour, arrêt et démarrage de
   ``geonature`` et ``geonature-worker``.
2. Dans ``config/geonature_config.toml``, ajoutez :

   .. code-block:: toml

       [UPDATER]
           ENABLED = true

3. Rechargez GeoNature : ``sudo systemctl restart geonature geonature-worker``.

Seuls les utilisateurs qui ont le droit « R » de portée 3 sur le module ``ADMIN`` voient la page. Il faut le
droit « U » de portée 3 pour lancer une opération.

**Mise à jour de GeoNature.** Le script de migration modifie ``/etc`` (systemd, Apache). La mise à jour de
GeoNature lui-même demande donc que l'utilisateur linux de GeoNature ait un sudo **sans mot de passe complet**.
Avec ce droit, une faille dans l'application web donnerait accès à tout le serveur : le bouton est donc
**désactivé par défaut**. Lancez plutôt la mise à jour en SSH (``./install/update/geonature-update.sh core
X.Y.Z``), ou activez le bouton en connaissance de cause avec ``ALLOW_CORE_UPDATE = true`` dans la section
``[UPDATER]``. Seule la dernière version publiée, plus récente que la version installée, est acceptée. Le
script se déroule ainsi :

* sauvegarde de la base avec ``pg_dump`` dans ``/var/lib/geonature/update/backups/``, sauf si
  ``UPDATER.BACKUP_DB = false``. Le mot de passe passe par un fichier ``.pgpass`` temporaire, jamais en
  ligne de commande. Seules les ``UPDATER.BACKUP_KEEP`` (5) dernières sauvegardes sont conservées ;
* installation faite depuis une archive : téléchargement de la nouvelle version, renommage de l'ancien dossier
  en ``geonature_old_<version>_<date>``, puis lancement de ``migration.sh`` ;
* installation faite avec git : ``git fetch``, ``git checkout <tag>``, ``git submodule update`` puis
  ``migration.sh .`` ;
* la migration n'est considérée comme réussie que si ``migration.sh`` affiche « Migration terminée », sans
  erreur ``npm ERR!`` ni exception Python dans sa sortie, que ``VERSION`` vaut la version cible, et que
  GeoNature répond ensuite sur ``/healthz`` (``migration.sh`` ne s'arrête pas toujours sur une erreur) ;
* si le service est arrêté pendant la mise à jour (``systemctl stop``, redémarrage du serveur), le script
  fait son retour arrière (jusqu'à 20 minutes, ``TimeoutStopSec``) et la page affiche l'échec. Si le script est
  tué brutalement, la page détecte qu'il ne tourne plus et affiche « interrompu » ;
* en cas d'échec ou d'interruption, l'ancienne version des fichiers est restaurée et les services sont
  redémarrés. **La base de données n'est pas restaurée automatiquement.** Si elle a déjà été migrée,
  restaurez la sauvegarde indiquée dans le journal. ``pg_restore --clean`` ne suffit pas, car il laisse les
  tables créées par les nouvelles migrations. La procédure complète est écrite dans le journal :

  .. code-block:: bash

      sudo systemctl stop geonature geonature-worker
      sudo -u postgres dropdb <base>
      sudo -u postgres createdb -O <propriétaire> <base>
      sudo -u postgres psql -d <base> -c 'CREATE EXTENSION IF NOT EXISTS postgis'
      pg_restore -h localhost -U <propriétaire> -d <base> <fichier.dump>
      sudo systemctl start geonature geonature-worker

Lisez toujours les notes de version avant de cliquer : certaines versions demandent des actions manuelles,
que le bouton ne fait pas à votre place.

**Mise à jour d'un module.** Le dossier du module doit être un clone git, ou le module doit déclarer son dépôt
GitHub dans ses métadonnées. Les modules fournis avec GeoNature (dossier ``contrib/``) se mettent à jour avec
GeoNature. Un clone git qui contient des modifications locales non commitées est refusé.

**Migration de Taxref.** La carte « Taxref » affiche la version installée. Elle propose la migration vers la
version suivante quand la version de TaxHub installée la connaît. Les migrations se font une version après
l'autre. La migration se fait en deux étapes, avec les commandes de TaxHub :

1. **Analyser** (``geonature taxref migrate-to-vNN import-taxref-vNN``) : la nouvelle version est importée dans
   des tables temporaires, sans toucher au Taxref en service. La page affiche le résultat et propose les
   rapports CSV en téléchargement : ``liste_changements.csv``, ``liste_split.csv``, cd_nom disparus encore
   utilisés… S'il y a des conflits bloquants, corrigez vos données puis relancez l'analyse. Si le blocage vient
   de cd_nom disparus, vous pouvez aussi cocher « conserver les cd_nom disparus ».
2. **Appliquer** (``geonature taxref migrate-to-vNN apply-changes --keep-oldtaxref --keep-oldbdc``) : ce bouton
   n'est disponible qu'après une analyse sans conflit bloquant, avec la même option. GeoNature est arrêté, la
   base est toujours sauvegardée avant, et les anciennes tables Taxref et statuts sont conservées en copie. La
   région utilisée est ``UPDATER.TAXREF_REGION`` (``fr`` par défaut).

Après la migration, installez le référentiel de sensibilité de la nouvelle version de Taxref (voir
:doc:`sensitivity`), puis lancez « Sensibilité : rafraîchir le cache des règles ».

**Limites.** Si vous changez ``UPDATER.STATE_DIR``, adaptez aussi le chemin passé à ``--from-request`` dans
``/etc/systemd/system/geonature-update.service``.
