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
# Avec --from-request FICHIER, les paramètres sont lus dans FICHIER (request.env écrit
# par l'interface d'administration), que le script analyse et revalide lui-même :
# lignes CLE="valeur", clés connues uniquement, sans caractère spécial.

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
REQUEST_FILE=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run) DRY_RUN=1; shift ;;
        --no-backup) BACKUP_DB=0; shift ;;
        --yes|-y) YES=1; shift ;;
        --from-request) FROM_REQUEST=1; YES=1; REQUEST_FILE="${2:-}"; shift 2 || shift ;;
        -h|--help) sed -n '2,18p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        --) shift; break ;;
        -*) echo "Option inconnue : $1" >&2; exit 2 ;;
        *) break ;;
    esac
done

# Lecture stricte du fichier de demande (pas de « source » : aucune expansion shell)
REQUEST_KEYS=" ACTION TARGET MODULE_CODE MODULE_REF MODULE_DIR MODULE_REPO GITHUB_REPO BACKUP_DB BACKUP_KEEP TAXREF_STEP TAXREF_VERSION TAXREF_KEEP_CDNOM TAXREF_REGION STATE_DIR LOG_DIR REQUESTED_BY "
read_request() {
    local file="$1" line key value
    if [[ -z "${file}" || ! -f "${file}" ]]; then
        echo "Fichier de demande introuvable : '${file}'" >&2
        exit 2
    fi
    while IFS= read -r line || [[ -n "${line}" ]]; do
        [[ -z "${line}" ]] && continue
        if [[ ! "${line}" =~ ^([A-Z_]+)=\"([^\"\\\$\`]*)\"$ ]]; then
            echo "Ligne invalide dans ${file} : ${line}" >&2
            exit 2
        fi
        key="${BASH_REMATCH[1]}"
        value="${BASH_REMATCH[2]}"
        if [[ "${REQUEST_KEYS}" != *" ${key} "* ]]; then
            echo "Clé inconnue dans ${file} : ${key}" >&2
            exit 2
        fi
        printf -v "${key}" '%s' "${value}"
    done < "${file}"
}

if [[ "${FROM_REQUEST}" == 1 ]]; then
    read_request "${REQUEST_FILE}"
else
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
BACKUP_KEEP="${BACKUP_KEEP:-5}"
TAXREF_REGION="${TAXREF_REGION:-fr}"
STATE_DIR="${STATE_DIR:-/var/lib/geonature/update}"
LOG_DIR="${LOG_DIR:-/var/log/geonature/update}"

# --- Journal et statut ------------------------------------------------------------
[[ "${STATE_DIR}" == /* && "${LOG_DIR}" == /* ]] || { echo "STATE_DIR et LOG_DIR doivent être absolus" >&2; exit 2; }
[[ "${STATE_DIR}" != *..* && "${LOG_DIR}" != *..* ]] || { echo "Chemin invalide" >&2; exit 2; }
TS="$(date +%Y%m%d-%H%M%S)"
mkdir -p "${STATE_DIR}" "${LOG_DIR}"
LOG_FILE="${LOG_DIR}/${TS}-${ACTION//[^a-z]/}-$$.log"
STATUS_FILE="${STATE_DIR}/status.json"
STARTED_AT="$(date -Iseconds)"
TARGET_LABEL="${TARGET}"
if [[ "${ACTION}" == module ]]; then TARGET_LABEL="${MODULE_CODE}:${MODULE_REF}"; fi
RUNNER=cli
if [[ "${FROM_REQUEST}" == 1 ]]; then RUNNER=service; fi

json_escape() {
    local s="$1"
    s="${s//\\/\\\\}"; s="${s//\"/\\\"}"; s="${s//$'\n'/ }"; s="${s//$'\t'/ }"
    printf '%s' "${s}"
}

write_status() {  # write_status STATE [MESSAGE]
    local state="$1" message="${2:-}" finished=""
    if [[ "${state}" != running ]]; then finished=",\"finished_at\":\"$(date -Iseconds)\""; fi
    if [[ "${DRY_RUN}" == 1 ]]; then message="[dry-run] ${message}"; fi
    printf '{"state":"%s","action":"%s","target":"%s","message":"%s","log_file":"%s","started_at":"%s","pid":%s,"runner":"%s"%s}\n' \
        "${state}" "$(json_escape "${ACTION}")" "$(json_escape "${TARGET_LABEL}")" "$(json_escape "${message}")" \
        "$(json_escape "${LOG_FILE}")" "${STARTED_AT}" "$$" "${RUNNER}" "${finished}" > "${STATUS_FILE}.tmp"
    mv "${STATUS_FILE}.tmp" "${STATUS_FILE}"
}

# --- Validation (le backend valide déjà, on revalide ici) -----------------------
die_usage() {
    echo "Erreur : $*" >&2
    # Lancé par le service : la demande « queued » est la nôtre, on la marque en échec.
    if [[ "${FROM_REQUEST}" == 1 ]]; then write_status failed "Demande invalide : $*"; fi
    exit 2
}
re_version='^[0-9]+\.[0-9]+\.[0-9]+([.-]?(a|b|rc|alpha|beta)\.?[0-9]+)?$'
re_code='^[A-Z0-9_]{1,50}$'
re_ref='^[A-Za-z0-9][A-Za-z0-9._/-]{0,99}$'
re_repo='^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$'
case "${ACTION}" in
    core)
        [[ "${TARGET}" =~ ${re_version} ]] || die_usage "version invalide : '${TARGET}'"
        current_version="$(cat "${BASE_DIR}/VERSION" 2>/dev/null || echo 0)"
        if [[ "$(printf '%s\n%s\n' "${current_version}" "${TARGET}" | sort -V | tail -n1)" != "${TARGET}" \
            || "${current_version}" == "${TARGET}" ]]; then
            die_usage "la version ${TARGET} n'est pas plus récente que la version installée (${current_version})"
        fi
        [[ "${GITHUB_REPO}" =~ ${re_repo} ]] || die_usage "dépôt GitHub invalide"
        ;;
    module)
        [[ "${MODULE_CODE}" =~ ${re_code} ]] || die_usage "code de module invalide"
        [[ "${MODULE_REF}" =~ ${re_ref} && "${MODULE_REF}" != *..* ]] || die_usage "référence invalide"
        [[ -z "${MODULE_REPO}" || "${MODULE_REPO}" =~ ${re_repo} ]] || die_usage "dépôt du module invalide"
        [[ -z "${MODULE_DIR}" || "${MODULE_DIR}" == /* ]] || die_usage "dossier du module invalide"
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
[[ "${BACKUP_KEEP}" =~ ^[0-9]{1,3}$ ]] || die_usage "BACKUP_KEEP invalide"

# --- Verrou -------------------------------------------------------------------
exec 9>"${STATE_DIR}/update.lock"
if ! flock -n 9; then
    echo "Une mise à jour est déjà en cours, arrêt." >&2
    exit 3
fi

exec > >(tee -a "${LOG_FILE}") 2>&1

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
# Arrêt du service, Ctrl+C, fermeture du terminal : retour arrière et statut « failed »
on_signal() {
    FAIL_MESSAGE="Mise à jour interrompue (signal $1 reçu). Consultez le journal${DB_DUMP:+ ; sauvegarde de la base : ${DB_DUMP}}."
    false || on_error
}
trap 'on_signal TERM' TERM
trap 'on_signal INT' INT
trap 'on_signal HUP' HUP

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
    # Si quoi que ce soit échoue ensuite, les services sont au moins redémarrés.
    if [[ -z "${ROLLBACK}" ]]; then ROLLBACK=start_services; fi
    local s
    for s in "${SERVICES[@]}"; do run sudo -n systemctl stop "${s}"; done
}

start_services() {
    step "Démarrage des services"
    local s
    for s in "${SERVICES[@]}"; do run sudo -n systemctl start "${s}"; done
}

DB_HOST="" DB_PORT="" DB_USER="" DB_NAME=""
load_db_params() {  # à appeler venv activé ; ne garde jamais le mot de passe dans une variable exportée
    if [[ -n "${DB_NAME}" ]]; then return 0; fi
    if [[ "${DRY_RUN}" == 1 ]]; then
        DB_HOST="<hôte>" DB_PORT="<port>" DB_USER="<propriétaire>" DB_NAME="<base>"
        return 0
    fi
    local params
    mapfile -t params < <(python3 - <<'PY'
from urllib.parse import urlparse, unquote
from geonature.utils.config import config
u = urlparse(config["SQLALCHEMY_DATABASE_URI"])
print(u.hostname or "localhost")
print(u.port or 5432)
print(unquote(u.username or ""))
print(unquote(u.path.lstrip("/")))
PY
)
    if [[ ${#params[@]} -lt 4 ]]; then
        FAIL_MESSAGE="Impossible de lire les paramètres de connexion à la base (geonature_config.toml)."
        return 1
    fi
    DB_HOST="${params[0]}" DB_PORT="${params[1]}" DB_USER="${params[2]}" DB_NAME="${params[3]}"
}

restore_hint() {  # procédure de restauration complète de la sauvegarde
    [[ -n "${DB_DUMP:-}" ]] || return 0
    echo "Pour revenir à l'état de la base avant la mise à jour :"
    echo "  sudo systemctl stop geonature geonature-worker"
    echo "  sudo -u postgres dropdb ${DB_NAME:-<base>}"
    echo "  sudo -u postgres createdb -O ${DB_USER:-<propriétaire>} ${DB_NAME:-<base>}"
    echo "  sudo -u postgres psql -d ${DB_NAME:-<base>} -c 'CREATE EXTENSION IF NOT EXISTS postgis'"
    echo "  pg_restore -h ${DB_HOST:-localhost} -U ${DB_USER:-<propriétaire>} -d ${DB_NAME:-<base>} ${DB_DUMP}"
    echo "  sudo systemctl start geonature geonature-worker"
}

backup_db() {
    if [[ "${BACKUP_DB}" != 1 ]]; then
        echo "Sauvegarde de la base désactivée."
        return
    fi
    step "Sauvegarde de la base de données"
    mkdir -p "${STATE_DIR}/backups"
    local dump passfile
    dump="${STATE_DIR}/backups/geonature-${TS}.dump"
    load_db_params
    echo "+ pg_dump -Fc -h ${DB_HOST} -p ${DB_PORT} -U ${DB_USER} -f ${dump} ${DB_NAME}"
    if [[ "${DRY_RUN}" == 0 ]]; then
        # Mot de passe dans un fichier .pgpass temporaire (0600), jamais en argument (visible par ps)
        passfile="$(umask 077; mktemp)"
        python3 - "${passfile}" <<'PY'
import sys
from urllib.parse import urlparse, unquote
from geonature.utils.config import config
u = urlparse(config["SQLALCHEMY_DATABASE_URI"])
esc = lambda v: str(v).replace("\\", "\\\\").replace(":", "\\:")
with open(sys.argv[1], "w") as f:
    f.write(":".join(esc(x) for x in (
        u.hostname or "localhost", u.port or 5432, unquote(u.path.lstrip("/")),
        unquote(u.username or ""), unquote(u.password or ""))) + "\n")
PY
        local rc=0
        (umask 077; PGPASSFILE="${passfile}" pg_dump -Fc -h "${DB_HOST}" -p "${DB_PORT}" \
            -U "${DB_USER}" -f "${dump}" "${DB_NAME}") || rc=$?
        rm -f "${passfile}"
        if [[ ${rc} != 0 ]]; then
            FAIL_MESSAGE="Échec de la sauvegarde de la base (pg_dump), mise à jour annulée."
            return "${rc}"
        fi
    fi
    DB_DUMP="${dump}"
    echo "Sauvegarde : ${dump}"
    # Ne garder que les BACKUP_KEEP sauvegardes les plus récentes
    if [[ "${DRY_RUN}" == 0 && "${BACKUP_KEEP}" -gt 0 ]]; then
        find "${STATE_DIR}/backups" -maxdepth 1 -name 'geonature-*.dump' -printf '%T@ %p\n' \
            | sort -rn | tail -n +"$((BACKUP_KEEP + 1))" | cut -d' ' -f2- | xargs -r rm -f --
    fi
}

run_migration() {  # run_migration DOSSIER_GEONATURE ANCIEN_DOSSIER VERSION_ATTENDUE
    local dir="$1" old="$2" expected="$3" out
    out="$(mktemp)"
    if [[ "${DRY_RUN}" == 1 ]]; then
        echo "+ GN_MIGRATION_NONINTERACTIVE=1 ${dir}/install/migration/migration.sh ${old} < (yes y)"
        rm -f "${out}"
        return 0
    fi
    # « yes y » répond à la confirmation des versions officielles de migration.sh, qui ne
    # connaissent pas GN_MIGRATION_NONINTERACTIVE (sous systemd, stdin = /dev/null).
    (cd "${dir}" && GN_MIGRATION_NONINTERACTIVE=1 ./install/migration/migration.sh "${old}" < <(yes y)) 2>&1 \
        | tee "${out}"
    # migration.sh n'a pas de « set -e » : son code de sortie ne suffit pas.
    if ! grep -q "Migration terminée" "${out}"; then
        rm -f "${out}"
        FAIL_MESSAGE="La migration ne s'est pas terminée (ligne « Migration terminée » absente). Consultez le journal."
        return 1
    fi
    rm -f "${out}"
    if [[ "$(cat "${dir}/VERSION")" != "${expected}" ]]; then
        FAIL_MESSAGE="Après migration, la version installée n'est pas ${expected}."
        return 1
    fi
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
    load_db_params
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
        echo "Si la base de données a déjà été migrée :"
        restore_hint
        FAIL_MESSAGE="${FAIL_MESSAGE:-Échec de la migration.} Les fichiers de la version ${current} ont été restaurés${DB_DUMP:+ ; si la base a déjà été migrée, restaurez la sauvegarde ${DB_DUMP} (procédure dans le journal)}."
    }
    ROLLBACK=rollback_core
    run mv "${BASE_DIR}" "${olddir}"
    run mv "${newdir}" "${BASE_DIR}"

    step "Migration (install/migration/migration.sh)"
    run_migration "${BASE_DIR}" "${olddir}" "${version}"
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
    load_db_params
    stop_services
    backup_db
    deactivate

    rollback_core_git() {
        git -C "${BASE_DIR}" checkout --detach "${previous}"
        git -C "${BASE_DIR}" submodule update --init --recursive
        # Le venv et le frontend ont pu être reconstruits pour la nouvelle version.
        (cd "${BASE_DIR}/install" && ./01_install_backend.sh) < /dev/null || echo "!! Réinstallation du backend impossible"
        (activate_venv && geonature update-configuration) || echo "!! Rebuild du frontend impossible"
        start_services
        echo "Si la base de données a déjà été migrée :"
        restore_hint
        FAIL_MESSAGE="${FAIL_MESSAGE:-Échec de la migration.} Retour au commit ${previous:0:10} (version ${current})${DB_DUMP:+ ; si la base a déjà été migrée, restaurez la sauvegarde ${DB_DUMP} (procédure dans le journal)}."
    }
    ROLLBACK=rollback_core_git
    run git -C "${BASE_DIR}" checkout --detach "refs/tags/${version}"
    run git -C "${BASE_DIR}" submodule update --init --recursive

    step "Migration (install/migration/migration.sh .)"
    run_migration "${BASE_DIR}" "${BASE_DIR}" "${version}"
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
    load_db_params
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
        # Ordre : tag, puis branche distante (jamais une branche locale périmée), puis commit
        local ref=""
        if git -C "${dir}" rev-parse --verify --quiet "refs/tags/${MODULE_REF}^{commit}" >/dev/null; then
            ref="refs/tags/${MODULE_REF}"
        elif git -C "${dir}" rev-parse --verify --quiet "refs/remotes/origin/${MODULE_REF}^{commit}" >/dev/null; then
            ref="refs/remotes/origin/${MODULE_REF}"
        elif [[ "${MODULE_REF}" =~ ^[0-9a-f]{7,40}$ ]] \
            && git -C "${dir}" rev-parse --verify --quiet "${MODULE_REF}^{commit}" >/dev/null; then
            ref="${MODULE_REF}"
        elif [[ "${DRY_RUN}" == 0 ]]; then
            FAIL_MESSAGE="Référence ${MODULE_REF} introuvable (ni tag, ni branche distante, ni commit) dans ${dir}."
            false
        fi
        rollback_module() {
            run git -C "${dir}" checkout --detach "${previous}"
            activate_venv
            geonature install-gn-module "${dir}" "${MODULE_CODE}" --build=false --upgrade-db=false || true
            geonature update-configuration || echo "!! Rebuild du frontend impossible"
            deactivate || true
            start_services
            restore_hint
            FAIL_MESSAGE="Échec de la mise à jour du module ${MODULE_CODE}, retour au commit ${previous:0:10}${DB_DUMP:+ ; si la base du module a déjà été migrée, restaurez la sauvegarde ${DB_DUMP} (procédure dans le journal)}."
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
        if [[ ! "${MODULE_REF}" =~ ^v?[0-9]+(\.[0-9]+)*([.-][A-Za-z0-9]+)*$ ]]; then
            FAIL_MESSAGE="Le module ${MODULE_CODE} n'est pas un clone git : seul un tag de version est accepté."
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
            geonature update-configuration || echo "!! Rebuild du frontend impossible"
            deactivate || true
            start_services
            restore_hint
            FAIL_MESSAGE="Échec de la mise à jour du module ${MODULE_CODE}, ancienne version restaurée${DB_DUMP:+ ; si la base du module a déjà été migrée, restaurez la sauvegarde ${DB_DUMP} (procédure dans le journal)}."
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
    load_db_params
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
    load_db_params
    stop_services
    BACKUP_DB=1 backup_db  # sauvegarde toujours faite avant de toucher à Taxref
    rollback_taxref() {
        start_services
        restore_hint
        FAIL_MESSAGE="La migration vers Taxref v${version} n'est pas allée au bout. La base peut être dans un état intermédiaire : restaurez la sauvegarde ${DB_DUMP:-(aucune)} (procédure dans le journal)."
    }
    ROLLBACK=rollback_taxref
    local opts=(--keep-oldtaxref --keep-oldbdc)
    # --taxref-region n'existe qu'à partir de la commande de migration vers la v17
    if [[ "${version}" -ge 17 ]]; then opts+=(--taxref-region "${TAXREF_REGION}"); fi
    if [[ "${TAXREF_KEEP_CDNOM}" == 1 ]]; then opts+=(--keep-cdnom); fi
    step "Application de la migration vers Taxref v${version}"
    run "${cli[@]}" apply-changes "${opts[@]}" 2>&1 | tee "${out}"
    # TaxHub journalise certaines erreurs sans s'arrêter, puis écrit « Import terminé ».
    if [[ "${DRY_RUN}" == 0 ]] && { ! grep -q "Import terminé" "${out}" \
        || grep -q "taxref_migration - ERROR" "${out}"; }; then
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
