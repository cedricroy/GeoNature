#!/usr/bin/env bash
#
# Mise à jour de GeoNature, de ses modules, de la base, du frontend et des référentiels.
#
# Ce script est lancé par le service systemd geonature-update.service (bouton
# « Mises à jour » de l'interface d'administration) avec l'option --from-request,
# mais il peut aussi être lancé à la main :
#
#   ./install/update/geonature-update.sh [--dry-run] [--no-backup] core 2.17.6
#   ./install/update/geonature-update.sh module MONITORINGS 1.2.3 [/chemin/du/module]
#   ./install/update/geonature-update.sh db
#   ./install/update/geonature-update.sh frontend
#   ./install/update/geonature-update.sh ref sensitivity-cache|sensitivity-synthese|profiles
#   ./install/update/geonature-update.sh taxref analyse:18[:keep]   # étape 1 : analyse
#   ./install/update/geonature-update.sh taxref apply:18[:keep]     # étape 2 : application
#     (:keep = conserver les cd_nom disparus ; région Taxref : variable TAXREF_REGION, fr par défaut)
#
# Avec --from-request, les paramètres sont lus dans les variables d'environnement
# ACTION, TARGET, MODULE_CODE, MODULE_REF, MODULE_DIR, MODULE_REPO, GITHUB_REPO et
# BACKUP_DB, chargées par systemd depuis STATE_DIR/request.env.

set -Eeuo pipefail

SERVICES=("geonature" "geonature-worker")

# --- Copie du script hors du dossier d'installation --------------------------
# Une mise à jour de GeoNature renomme le dossier d'installation : on s'exécute
# depuis une copie pour ne pas dépendre de ce dossier.
if [[ -z "${GN_UPDATE_BASE_DIR:-}" ]]; then
    GN_UPDATE_BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
    export GN_UPDATE_BASE_DIR
    copy="$(mktemp "${TMPDIR:-/tmp}/geonature-update.XXXXXX.sh")"
    cp "${BASH_SOURCE[0]}" "${copy}"
    chmod 700 "${copy}"
    export GN_UPDATE_SELF_COPY="${copy}"
    exec bash "${copy}" "$@"
fi
BASE_DIR="${GN_UPDATE_BASE_DIR}"
trap '[[ -n "${GN_UPDATE_SELF_COPY:-}" ]] && rm -f "${GN_UPDATE_SELF_COPY}"' EXIT

if [[ "$(id -u)" == "0" ]]; then
    echo "Ce script ne doit pas être lancé en root." >&2
    exit 1
fi

# --- Arguments ---------------------------------------------------------------
DRY_RUN=0
YES=0
FROM_REQUEST=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run) DRY_RUN=1; shift ;;
        --no-backup) BACKUP_DB=0; shift ;;
        --yes|-y) YES=1; shift ;;
        --from-request) FROM_REQUEST=1; YES=1; shift ;;
        -h|--help) sed -n '2,18p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        --) shift; break ;;
        -*) echo "Option inconnue : $1" >&2; exit 2 ;;
        *) break ;;
    esac
done

if [[ "${FROM_REQUEST}" == 0 ]]; then
    ACTION="${1:-}"
    case "${ACTION}" in
        core|ref|taxref) TARGET="${2:-}" ;;
        module) MODULE_CODE="${2:-}"; MODULE_REF="${3:-}"; MODULE_DIR="${4:-}" ;;
        db|frontend) ;;
        *) echo "Usage : $0 [--dry-run] [--no-backup] [--yes] {core VERSION|module CODE REF [DIR]|db|frontend|ref NOM|taxref ETAPE:VERSION[:keep]}" >&2; exit 2 ;;
    esac
fi
ACTION="${ACTION:-}"
TARGET="${TARGET:-}"
MODULE_CODE="${MODULE_CODE:-}"
MODULE_REF="${MODULE_REF:-}"
MODULE_DIR="${MODULE_DIR:-}"
MODULE_REPO="${MODULE_REPO:-}"
GITHUB_REPO="${GITHUB_REPO:-PnX-SI/GeoNature}"
BACKUP_DB="${BACKUP_DB:-1}"
TAXREF_REGION="${TAXREF_REGION:-fr}"
STATE_DIR="${STATE_DIR:-/var/lib/geonature/update}"
LOG_DIR="${LOG_DIR:-/var/log/geonature/update}"

# --- Validation (le backend valide déjà, on revalide ici) -----------------------
die_usage() { echo "Erreur : $*" >&2; exit 2; }
re_version='^[0-9]+\.[0-9]+\.[0-9]+([.-]?(a|b|rc|alpha|beta)\.?[0-9]+)?$'
re_code='^[A-Z0-9_]{1,50}$'
re_ref='^[A-Za-z0-9][A-Za-z0-9._/-]{0,99}$'
re_repo='^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$'
case "${ACTION}" in
    core)
        [[ "${TARGET}" =~ ${re_version} ]] || die_usage "version invalide : '${TARGET}'"
        [[ "${GITHUB_REPO}" =~ ${re_repo} ]] || die_usage "dépôt GitHub invalide"
        ;;
    module)
        [[ "${MODULE_CODE}" =~ ${re_code} ]] || die_usage "code de module invalide"
        [[ "${MODULE_REF}" =~ ${re_ref} && "${MODULE_REF}" != *..* ]] || die_usage "référence invalide"
        [[ -z "${MODULE_REPO}" || "${MODULE_REPO}" =~ ${re_repo} ]] || die_usage "dépôt du module invalide"
        ;;
    ref)
        case "${TARGET}" in
            sensitivity-cache|sensitivity-synthese|profiles) ;;
            *) die_usage "référentiel inconnu : '${TARGET}'" ;;
        esac
        ;;
    taxref)
        [[ "${TARGET}" =~ ^(analyse|apply):([0-9]{2})(:keep)?$ ]] || die_usage "demande Taxref invalide : '${TARGET}'"
        TAXREF_STEP="${BASH_REMATCH[1]}"
        TAXREF_VERSION="${BASH_REMATCH[2]}"
        TAXREF_KEEP_CDNOM=0
        if [[ -n "${BASH_REMATCH[3]}" ]]; then TAXREF_KEEP_CDNOM=1; fi
        [[ "${TAXREF_REGION}" =~ ^[a-z]{2,5}$ ]] || die_usage "région Taxref invalide"
        ;;
    db|frontend) ;;
    *) die_usage "action inconnue : '${ACTION}'" ;;
esac

# --- Journal, verrou et statut ------------------------------------------------
TS="$(date +%Y%m%d-%H%M%S)"
mkdir -p "${STATE_DIR}" "${LOG_DIR}"
LOG_FILE="${LOG_DIR}/${TS}-${ACTION}-$$.log"
STATUS_FILE="${STATE_DIR}/status.json"
STARTED_AT="$(date -Iseconds)"
TARGET_LABEL="${TARGET}"
if [[ "${ACTION}" == module ]]; then TARGET_LABEL="${MODULE_CODE}:${MODULE_REF}"; fi

exec 9>"${STATE_DIR}/update.lock"
if ! flock -n 9; then
    echo "Une mise à jour est déjà en cours, arrêt." >&2
    exit 3
fi

exec > >(tee -a "${LOG_FILE}") 2>&1

json_escape() {
    local s="$1"
    s="${s//\\/\\\\}"; s="${s//\"/\\\"}"; s="${s//$'\n'/ }"; s="${s//$'\t'/ }"
    printf '%s' "${s}"
}

write_status() {  # write_status STATE [MESSAGE]
    local state="$1" message="${2:-}" finished=""
    [[ "${state}" != running ]] && finished=",\"finished_at\":\"$(date -Iseconds)\""
    [[ "${DRY_RUN}" == 1 ]] && message="[dry-run] ${message}"
    printf '{"state":"%s","action":"%s","target":"%s","message":"%s","log_file":"%s","started_at":"%s"%s}\n' \
        "${state}" "${ACTION}" "$(json_escape "${TARGET_LABEL}")" "$(json_escape "${message}")" \
        "$(json_escape "${LOG_FILE}")" "${STARTED_AT}" "${finished}" > "${STATUS_FILE}.tmp"
    mv "${STATUS_FILE}.tmp" "${STATUS_FILE}"
}

step() { echo; echo "==> $*"; }

run() {  # affiche et exécute une commande (ou l'affiche seulement en --dry-run)
    echo "+ $*"
    if [[ "${DRY_RUN}" == 0 ]]; then "$@"; fi
}

ROLLBACK=""
MAIN_PID="${BASHPID}"
on_error() {
    local code=$?
    # Dans un sous-shell, on laisse le shell principal gérer l'erreur.
    if [[ "${BASHPID}" != "${MAIN_PID}" ]]; then exit "${code}"; fi
    trap - ERR
    echo
    echo "!! Échec (code ${code})."
    if [[ -n "${ROLLBACK}" ]]; then
        echo "==> Retour à l'état précédent…"
        set +e
        "${ROLLBACK}"
        set -e
    fi
    write_status failed "${FAIL_MESSAGE:-Échec de la mise à jour, consultez le journal.}"
    exit "${code}"
}
trap on_error ERR

write_status running "Mise à jour en cours…"
echo "Mise à jour GeoNature – action '${ACTION}' ${TARGET_LABEL} – $(date)"
echo "Dossier GeoNature : ${BASE_DIR} (version $(cat "${BASE_DIR}/VERSION" 2>/dev/null || echo '?'))"

if [[ "${FROM_REQUEST}" == 0 && "${YES}" == 0 && "${DRY_RUN}" == 0 ]]; then
    read -r -p "Continuer ? [y/N] " choice
    [[ "${choice}" == y || "${choice}" == Y ]] || { write_status failed "Annulé."; exit 0; }
fi

# --- Utilitaires --------------------------------------------------------------
activate_venv() {
    # shellcheck disable=SC1091
    source "${BASE_DIR}/backend/venv/bin/activate"
}

stop_services() {
    step "Arrêt des services"
    local s
    for s in "${SERVICES[@]}"; do run sudo -n systemctl stop "${s}"; done
}

start_services() {
    step "Démarrage des services"
    local s
    for s in "${SERVICES[@]}"; do run sudo -n systemctl start "${s}"; done
}

backup_db() {
    if [[ "${BACKUP_DB}" != 1 ]]; then
        echo "Sauvegarde de la base désactivée."
        return
    fi
    step "Sauvegarde de la base de données"
    mkdir -p "${STATE_DIR}/backups"
    local uri dump
    uri="$(geonature get-config SQLALCHEMY_DATABASE_URI)"
    uri="${uri/+psycopg2/}"
    dump="${STATE_DIR}/backups/geonature-${TS}.dump"
    echo "+ pg_dump -Fc -f ${dump} <base GeoNature>"
    if [[ "${DRY_RUN}" == 0 ]]; then
        (umask 077; pg_dump -Fc -f "${dump}" -d "${uri}")
    fi
    DB_DUMP="${dump}"
    echo "Sauvegarde : ${dump}"
}

download_archive() {  # download_archive REPO TAG DEST_DIR
    local repo="$1" tag="$2" dest="$3" tmp
    tmp="$(mktemp -d)"
    run curl -fsSL --retry 3 -o "${tmp}/archive.tar.gz" \
        "https://github.com/${repo}/archive/refs/tags/${tag}.tar.gz"
    run mkdir -p "${dest}"
    run tar -xzf "${tmp}/archive.tar.gz" -C "${dest}" --strip-components=1
    rm -rf "${tmp}"
}

# --- Actions ------------------------------------------------------------------
do_core() {
    local version="${TARGET}" parent current newdir olddir
    current="$(cat "${BASE_DIR}/VERSION")"
    parent="$(dirname "${BASE_DIR}")"
    newdir="${parent}/geonature_new_${version}_${TS}"
    olddir="${parent}/geonature_old_${current}_${TS}"

    if ! sudo -n true 2>/dev/null; then
        FAIL_MESSAGE="La mise à jour de GeoNature demande un sudo sans mot de passe pour l'utilisateur $(id -un) (le script de migration modifie /etc). Voir la documentation."
        false
    fi

    if [[ -d "${BASE_DIR}/.git" ]]; then
        do_core_git "${version}" "${current}"
        return
    fi

    step "Téléchargement de GeoNature ${version}"
    download_archive "${GITHUB_REPO}" "${version}" "${newdir}"
    if [[ "${DRY_RUN}" == 0 && ! -f "${newdir}/install/migration/migration.sh" ]]; then
        FAIL_MESSAGE="Archive téléchargée invalide."
        false
    fi

    activate_venv
    stop_services
    backup_db
    deactivate

    step "Remplacement du dossier ${BASE_DIR}"
    rollback_core() {
        echo "Restauration de ${olddir} → ${BASE_DIR}"
        if [[ -d "${olddir}" ]]; then
            if [[ -d "${BASE_DIR}" ]]; then
                # Les médias ont pu être déplacés vers le nouveau dossier en fin de migration.
                if [[ -d "${BASE_DIR}/backend/media" ]]; then
                    mkdir -p "${olddir}/backend/media"
                    cp -a -n "${BASE_DIR}/backend/media/." "${olddir}/backend/media/"
                fi
                mv "${BASE_DIR}" "${parent}/geonature_failed_${version}_${TS}"
            fi
            mv "${olddir}" "${BASE_DIR}"
        fi
        start_services
        echo "Les fichiers ont été restaurés."
        if [[ -n "${DB_DUMP:-}" ]]; then
            echo "Si la base de données a déjà été migrée, restaurez la sauvegarde :"
            echo "  pg_restore --clean --if-exists -d <base> ${DB_DUMP}"
        fi
        FAIL_MESSAGE="Échec de la mise à jour vers ${version}. Les fichiers de la version ${current} ont été restaurés${DB_DUMP:+ ; sauvegarde de la base : ${DB_DUMP}}. Consultez le journal."
    }
    ROLLBACK=rollback_core
    run mv "${BASE_DIR}" "${olddir}"
    run mv "${newdir}" "${BASE_DIR}"

    step "Migration (install/migration/migration.sh)"
    if [[ "${DRY_RUN}" == 0 ]]; then
        (cd "${BASE_DIR}/install/migration" && GN_MIGRATION_NONINTERACTIVE=1 ./migration.sh "${olddir}")
    else
        echo "+ GN_MIGRATION_NONINTERACTIVE=1 ${BASE_DIR}/install/migration/migration.sh ${olddir}"
    fi
    ROLLBACK=""
    echo "Ancienne version conservée dans ${olddir} (à supprimer quand tout fonctionne)."
}

do_core_git() {  # installation de GeoNature faite avec git : mise à jour sur place
    local version="$1" current="$2" previous
    step "Mise à jour du dépôt git vers ${version}"
    if ! git -C "${BASE_DIR}" diff --quiet HEAD; then
        FAIL_MESSAGE="Le dossier ${BASE_DIR} contient des modifications locales non commitées, mise à jour annulée."
        false
    fi
    previous="$(git -C "${BASE_DIR}" rev-parse HEAD)"
    run git -C "${BASE_DIR}" fetch --tags --force origin
    if ! git -C "${BASE_DIR}" rev-parse --verify --quiet "refs/tags/${version}" >/dev/null && [[ "${DRY_RUN}" == 0 ]]; then
        FAIL_MESSAGE="Le tag ${version} est introuvable dans le dépôt git."
        false
    fi

    activate_venv
    stop_services
    backup_db
    deactivate

    rollback_core_git() {
        git -C "${BASE_DIR}" checkout --detach "${previous}"
        git -C "${BASE_DIR}" submodule update --init --recursive
        start_services
        FAIL_MESSAGE="Échec de la mise à jour vers ${version}, retour au commit ${previous:0:10} (version ${current})${DB_DUMP:+ ; sauvegarde de la base : ${DB_DUMP}}. Consultez le journal."
    }
    ROLLBACK=rollback_core_git
    run git -C "${BASE_DIR}" checkout --detach "refs/tags/${version}"
    run git -C "${BASE_DIR}" submodule update --init --recursive

    step "Migration (install/migration/migration.sh .)"
    if [[ "${DRY_RUN}" == 0 ]]; then
        (cd "${BASE_DIR}" && GN_MIGRATION_NONINTERACTIVE=1 ./install/migration/migration.sh .)
    else
        echo "+ GN_MIGRATION_NONINTERACTIVE=1 ./install/migration/migration.sh ."
    fi
    ROLLBACK=""
}

do_module() {
    local dir="${MODULE_DIR}" previous="" olddir="" newdir=""
    if [[ -z "${dir}" && -L "${BASE_DIR}/frontend/external_modules/${MODULE_CODE,,}" ]]; then
        dir="$(dirname "$(realpath "${BASE_DIR}/frontend/external_modules/${MODULE_CODE,,}")")"
    fi
    if [[ -z "${dir}" || ! -d "${dir}" ]]; then
        FAIL_MESSAGE="Dossier du module ${MODULE_CODE} introuvable."
        false
    fi
    dir="$(realpath "${dir}")"
    if [[ "${dir}" == "$(realpath "${BASE_DIR}")"/contrib/* ]]; then
        FAIL_MESSAGE="Le module ${MODULE_CODE} est fourni avec GeoNature : mettez à jour GeoNature."
        false
    fi
    echo "Dossier du module : ${dir}"

    activate_venv
    backup_db
    deactivate

    if [[ -d "${dir}/.git" ]]; then
        step "Récupération de ${MODULE_REF} (git)"
        if ! git -C "${dir}" diff --quiet HEAD; then
            FAIL_MESSAGE="Le dossier ${dir} contient des modifications locales non commitées, mise à jour annulée."
            false
        fi
        previous="$(git -C "${dir}" rev-parse HEAD)"
        run git -C "${dir}" fetch --tags --force origin
        local ref="${MODULE_REF}"
        if ! git -C "${dir}" rev-parse --verify --quiet "${ref}^{commit}" >/dev/null \
            && git -C "${dir}" rev-parse --verify --quiet "origin/${ref}^{commit}" >/dev/null; then
            ref="origin/${ref}"
        fi
        rollback_module() {
            run git -C "${dir}" checkout --detach "${previous}"
            activate_venv
            geonature install-gn-module "${dir}" "${MODULE_CODE}" --build=false --upgrade-db=false || true
            deactivate || true
            start_services
            FAIL_MESSAGE="Échec de la mise à jour du module ${MODULE_CODE}, retour au commit ${previous:0:10}. Consultez le journal."
        }
        stop_services
        ROLLBACK=rollback_module
        run git -C "${dir}" checkout --detach "${ref}"
        git -C "${dir}" submodule update --init --recursive || true
    else
        if [[ -z "${MODULE_REPO}" ]]; then
            FAIL_MESSAGE="Le module ${MODULE_CODE} n'est pas un clone git et son dépôt GitHub est inconnu."
            false
        fi
        newdir="${dir}_new_${TS}"
        olddir="${dir}_old_${TS}"
        step "Téléchargement de ${MODULE_REPO} ${MODULE_REF}"
        download_archive "${MODULE_REPO}" "${MODULE_REF}" "${newdir}"
        if [[ "${DRY_RUN}" == 0 && -d "${dir}/config" ]]; then
            mkdir -p "${newdir}/config"
            cp -n "${dir}"/config/*.toml "${newdir}/config/" 2>/dev/null || true
        fi
        rollback_module() {
            if [[ -d "${olddir}" ]]; then
                [[ -d "${dir}" ]] && mv "${dir}" "${dir}_failed_${TS}"
                mv "${olddir}" "${dir}"
            fi
            activate_venv
            geonature install-gn-module "${dir}" "${MODULE_CODE}" --build=false --upgrade-db=false || true
            deactivate || true
            start_services
            FAIL_MESSAGE="Échec de la mise à jour du module ${MODULE_CODE}, ancienne version restaurée. Consultez le journal."
        }
        stop_services
        ROLLBACK=rollback_module
        run mv "${dir}" "${olddir}"
        run mv "${newdir}" "${dir}"
    fi

    activate_venv
    step "Installation du module"
    run geonature install-gn-module "${dir}" "${MODULE_CODE}" --build=false --upgrade-db=false
    step "Mise à jour de la base du module"
    run geonature upgrade-modules-db "${MODULE_CODE}"
    run geonature db autoupgrade
    step "Configuration et rebuild du frontend"
    run geonature update-configuration
    deactivate
    ROLLBACK=""
    start_services
    if [[ -n "${olddir}" ]]; then
        echo "Ancienne version du module conservée dans ${olddir}."
    fi
}

do_db() {
    activate_venv
    backup_db
    step "Mise à jour de la base de données"
    run geonature db autoupgrade
    run geonature upgrade-modules-db
    deactivate
    step "Rechargement de GeoNature"
    run sudo -n systemctl restart geonature
}

do_frontend() {
    activate_venv
    step "Configuration et rebuild du frontend"
    run geonature update-configuration
    deactivate
}

do_ref() {
    activate_venv
    case "${TARGET}" in
        sensitivity-cache)
            step "Rafraîchissement du cache des règles de sensibilité"
            run geonature sensitivity refresh-rules-cache
            ;;
        sensitivity-synthese)
            step "Recalcul de la sensibilité de la synthèse"
            run geonature sensitivity update-synthese
            ;;
        profiles)
            step "Recalcul des profils de taxons"
            run geonature profiles update
            ;;
    esac
    deactivate
}

do_taxref() {
    local version="${TAXREF_VERSION}" workdir marker out blocked=0
    local cli=(geonature taxref "migrate-to-v${version}")
    workdir="${STATE_DIR}/taxref/v${version}"
    marker="${STATE_DIR}/taxref/analysis-v${version}.json"
    mkdir -p "${workdir}"
    out="$(mktemp)"
    activate_venv
    # TaxHub écrit ses rapports CSV dans ./tmp/ : on travaille dans un dossier dédié.
    cd "${workdir}"

    if [[ "${TAXREF_STEP}" == analyse ]]; then
        rm -rf "${workdir}/tmp" "${marker}"
        step "Analyse de la migration vers Taxref v${version} (Taxref en service inchangé)"
        run "${cli[@]}" "import-taxref-v${version}" 2>&1 | tee "${out}"
        if [[ "${TAXREF_KEEP_CDNOM}" == 1 ]]; then
            step "Nouvelle analyse en conservant les cd_nom disparus"
            run "${cli[@]}" test-changes-detection --keep-cdnom 2>&1 | tee "${out}"
        fi
        # En cas de conflit bloquant, TaxHub s'arrête avec le code 0 : on lit sa sortie.
        if grep -q "You can't continue migration" "${out}"; then blocked=1; fi
        if [[ "${DRY_RUN}" == 0 ]]; then
            printf '{"ok":%s,"keep_cdnom":%s,"date":"%s"}\n' \
                "$([[ ${blocked} == 0 ]] && echo true || echo false)" \
                "$([[ ${TAXREF_KEEP_CDNOM} == 1 ]] && echo true || echo false)" \
                "$(date '+%d/%m/%Y %H:%M')" > "${marker}"
        fi
        echo "Rapports : ${workdir}/tmp/"
        ls -1 "${workdir}/tmp/" 2>/dev/null || true
        rm -f "${out}"
        deactivate
        if [[ ${blocked} == 1 ]]; then
            FAIL_MESSAGE="Analyse terminée : conflits bloquants. Lisez les rapports CSV, corrigez puis relancez l'analyse."
            false
        fi
        echo "Analyse terminée : aucun conflit bloquant. Lisez les rapports avant d'appliquer la migration."
        return
    fi

    # Étape 2 : application
    if [[ "${DRY_RUN}" == 0 ]]; then
        if ! grep -q '"ok":true' "${marker}" 2>/dev/null; then
            FAIL_MESSAGE="Aucune analyse sans conflit bloquant pour Taxref v${version} : lancez d'abord l'analyse."
            false
        fi
        local expected=false
        if [[ "${TAXREF_KEEP_CDNOM}" == 1 ]]; then expected=true; fi
        if ! grep -q "\"keep_cdnom\":${expected}" "${marker}"; then
            FAIL_MESSAGE="L'option « conserver les cd_nom disparus » doit être la même que lors de l'analyse."
            false
        fi
    fi
    stop_services
    BACKUP_DB=1 backup_db  # sauvegarde toujours faite avant de toucher à Taxref
    rollback_taxref() {
        start_services
        FAIL_MESSAGE="La migration vers Taxref v${version} n'est pas allée au bout. La base peut être dans un état intermédiaire : restaurez la sauvegarde ${DB_DUMP:-(aucune)} avec pg_restore --clean --if-exists. Consultez le journal."
    }
    ROLLBACK=rollback_taxref
    local opts=(--keep-oldtaxref --keep-oldbdc --taxref-region "${TAXREF_REGION}")
    if [[ "${TAXREF_KEEP_CDNOM}" == 1 ]]; then opts+=(--keep-cdnom); fi
    step "Application de la migration vers Taxref v${version}"
    run "${cli[@]}" apply-changes "${opts[@]}" 2>&1 | tee "${out}"
    if [[ "${DRY_RUN}" == 0 ]] && ! grep -q "Import terminé" "${out}"; then
        rm -f "${out}"
        false
    fi
    rm -f "${out}" "${marker}"
    deactivate
    ROLLBACK=""
    start_services
    echo "Migration vers Taxref v${version} terminée."
    echo "Pensez à installer le référentiel de sensibilité de Taxref v${version}, puis à rafraîchir le cache des règles."
}

"do_${ACTION}"

write_status success "Terminé."
echo
echo "==> Terminé avec succès ($(date))."
