import json
import os
import re
import time
from pathlib import Path
from urllib.parse import urlparse, unquote

import requests
from flask import current_app

from geonature.utils.env import GEONATURE_VERSION, ROOT_DIR
from geonature.utils.module import iter_modules_dist

# Actions autorisées (liste blanche). Toute autre valeur est refusée.
ACTION_CORE = "core"
ACTION_MODULE = "module"
ACTION_DB = "db"
ACTION_FRONTEND = "frontend"
ACTION_REF = "ref"

# Les nouvelles révisions des données de référence déjà installées (nomenclatures,
# ref_geo, taxonomie…) sont appliquées par l'action « db » (geonature db autoupgrade).
REF_TARGETS = {
    "sensitivity-cache": "Sensibilité : rafraîchir le cache des règles",
    "sensitivity-synthese": "Sensibilité : recalculer la sensibilité des observations de la synthèse",
    "profiles": "Profils de taxons : recalculer les profils",
}

ACTION_TAXREF = "taxref"

ACTIONS = {ACTION_CORE, ACTION_MODULE, ACTION_DB, ACTION_FRONTEND, ACTION_REF, ACTION_TAXREF}

VERSION_RE = re.compile(r"^\d+\.\d+\.\d+([.-]?(a|b|rc|alpha|beta)\.?\d+)?$")
MODULE_CODE_RE = re.compile(r"^[A-Z0-9_]{1,50}$")
# Référence git (tag, branche, commit) : ne commence pas par « - » (injection
# d'option) et ne contient pas « .. ».
GIT_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,99}$")
GITHUB_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
# Pour un module installé par archive : seulement des tags de version (v1.2.3, 1.2.3-rc1…)
TAG_RE = re.compile(r"^v?\d+(\.\d+)*([.-][A-Za-z0-9]+)*$")
GITHUB_URL_RE = re.compile(r"^https://github\.com/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+?)(\.git)?/?$")

STATE_RUNNING = ("queued", "running")

_release_cache = {}
RELEASE_CACHE_SECONDS = 3600


class UpdateRequestError(ValueError):
    pass


def updater_config():
    return current_app.config["UPDATER"]


def state_dir():
    return Path(updater_config()["STATE_DIR"])


def status_file():
    return state_dir() / "status.json"


def request_file():
    return state_dir() / "request.env"


def installed_version():
    return GEONATURE_VERSION.strip()


def _dist_directory(dist):
    """Dossier source d'un module installé en mode éditable (pip install -e)."""
    try:
        direct_url = dist.read_text("direct_url.json")
    except Exception:
        direct_url = None
    if not direct_url:
        return None
    try:
        url = json.loads(direct_url).get("url", "")
    except ValueError:
        return None
    parsed = urlparse(url)
    if parsed.scheme != "file":
        return None
    return Path(unquote(parsed.path)).resolve()


def _dist_github_repo(dist):
    candidates = []
    meta = dist.metadata
    if meta.get("Home-page"):
        candidates.append(meta.get("Home-page"))
    for project_url in meta.get_all("Project-URL") or []:
        candidates.append(project_url.split(",", 1)[-1].strip())
    for url in candidates:
        match = GITHUB_URL_RE.match(url.strip())
        if match:
            return match.group(1)
    return None


def list_modules():
    """Modules GeoNature installés, avec ce qu'il faut pour les mettre à jour."""
    modules = []
    contrib_dir = (ROOT_DIR / "contrib").resolve()
    for dist in iter_modules_dist():
        try:
            code = dist.entry_points["code"].load()
        except Exception:
            continue
        directory = _dist_directory(dist)
        bundled = directory is not None and contrib_dir in (directory, *directory.parents)
        is_git = directory is not None and (directory / ".git").exists()
        repo = _dist_github_repo(dist)
        modules.append(
            {
                "code": code,
                "name": dist.metadata.get("Name"),
                "version": dist.version,
                "directory": str(directory) if directory else None,
                "bundled": bundled,
                "git": is_git,
                "github_repo": repo,
                # Les modules de contrib/ sont mis à jour avec GeoNature.
                "updatable": (not bundled) and directory is not None and (is_git or bool(repo)),
            }
        )
    return sorted(modules, key=lambda m: m["code"])


def get_module(code):
    for module in list_modules():
        if module["code"] == code:
            return module
    return None


def latest_release(repo=None):
    """Dernière release GitHub (mise en cache une heure). None si indisponible."""
    repo = repo or updater_config()["GITHUB_REPO"]
    if not GITHUB_REPO_RE.match(repo):
        return None
    cached = _release_cache.get(repo)
    if cached and time.monotonic() - cached[0] < RELEASE_CACHE_SECONDS:
        return cached[1]
    try:
        response = requests.get(
            f"https://api.github.com/repos/{repo}/releases/latest",
            headers={"Accept": "application/vnd.github+json"},
            timeout=5,
        )
        response.raise_for_status()
        data = response.json()
        release = {
            "version": data.get("tag_name", "").lstrip("v"),
            "url": data.get("html_url"),
            "published_at": data.get("published_at"),
            "notes": data.get("body") or "",
        }
    except (requests.RequestException, ValueError):
        release = None
    _release_cache[repo] = (time.monotonic(), release)
    return release


def _version_tuple(version):
    return tuple(int(part) for part in re.findall(r"\d+", version)[:3])


def is_newer(candidate, current):
    try:
        return _version_tuple(candidate) > _version_tuple(current)
    except ValueError:
        return False


def read_status():
    try:
        with open(status_file()) as f:
            status = json.load(f)
    except (OSError, ValueError):
        return {"state": "idle"}
    if not isinstance(status, dict):
        return {"state": "idle"}
    return status


def write_status(**status):
    state_dir().mkdir(parents=True, exist_ok=True)
    tmp = status_file().with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(status, f)
    tmp.replace(status_file())


QUEUED_TIMEOUT_SECONDS = 600


def process_is_alive(pid):
    """Le processus du script de mise à jour existe-t-il encore ? (même utilisateur)"""
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except (TypeError, ValueError, OSError):
        return None
    return True


def is_busy():
    """
    Vrai si une mise à jour tourne. Une demande restée « queued » plus de
    10 minutes (worker Celery arrêté, par exemple) ne bloque plus l'outil, ni un
    état « running » alors que le script n'existe plus (tué, serveur redémarré).
    """
    status = read_status()
    state = status.get("state")
    if state == "running":
        if status.get("pid") and process_is_alive(status["pid"]) is False:
            write_status(
                **dict(
                    status,
                    state="failed",
                    message="Mise à jour interrompue (le script s'est arrêté avant la fin). "
                    "Consultez le journal.",
                )
            )
            return False
        return True
    if state == "queued":
        try:
            return time.time() - float(status.get("queued_ts", 0)) < QUEUED_TIMEOUT_SECONDS
        except (TypeError, ValueError):
            return False
    return False


def read_log_tail(max_lines=200):
    log_file = read_status().get("log_file")
    if not log_file:
        return []
    log_path = Path(log_file).resolve()
    log_dir = Path(updater_config()["LOG_DIR"]).resolve()
    if log_dir not in log_path.parents:  # ne lit que dans LOG_DIR
        return []
    try:
        with open(log_path, "rb") as f:
            # Lit seulement la fin du fichier (le journal peut être long).
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 200 * max_lines))
            data = f.read().decode(errors="replace")
    except OSError:
        return []
    lines = data.splitlines()
    if size > 200 * max_lines and lines:
        lines = lines[1:]  # première ligne probablement tronquée
    return lines[-max_lines:]


def validate_request(action, target=None):
    """
    Valide une demande et renvoie les variables à écrire dans request.env.
    Lève UpdateRequestError si la demande n'est pas acceptable.
    """
    target = (target or "").strip()
    cfg = updater_config()
    if action not in ACTIONS:
        raise UpdateRequestError("Action inconnue.")
    env = {
        "ACTION": action,
        "BACKUP_DB": "1" if cfg["BACKUP_DB"] else "0",
        "STATE_DIR": cfg["STATE_DIR"],
        "LOG_DIR": cfg["LOG_DIR"],
        "BACKUP_KEEP": str(int(cfg["BACKUP_KEEP"])),
    }

    if action == ACTION_CORE:
        if not cfg["ALLOW_CORE_UPDATE"]:
            raise UpdateRequestError(
                "La mise à jour de GeoNature depuis l'interface est désactivée "
                "(UPDATER.ALLOW_CORE_UPDATE). Utilisez la commande "
                "./install/update/geonature-update.sh core <version> sur le serveur."
            )
        if not VERSION_RE.match(target):
            raise UpdateRequestError("Version invalide (format attendu : X.Y.Z).")
        release = latest_release()
        if not release or target != release["version"]:
            raise UpdateRequestError(
                "Seule la dernière version publiée peut être installée depuis l'interface."
            )
        if not is_newer(target, installed_version()):
            raise UpdateRequestError(
                f"La version {target} n'est pas plus récente que la version installée."
            )
        if not GITHUB_REPO_RE.match(cfg["GITHUB_REPO"]):
            raise UpdateRequestError("Paramètre UPDATER.GITHUB_REPO invalide.")
        env["TARGET"] = target
        env["GITHUB_REPO"] = cfg["GITHUB_REPO"]
    elif action == ACTION_MODULE:
        code, _, ref = target.partition(":")
        if not MODULE_CODE_RE.match(code):
            raise UpdateRequestError("Code de module invalide.")
        if not GIT_REF_RE.match(ref) or ".." in ref:
            raise UpdateRequestError("Version / référence git du module invalide.")
        module = get_module(code)
        if module is None:
            raise UpdateRequestError(f"Le module {code} n'est pas installé.")
        if not module["updatable"]:
            raise UpdateRequestError(
                f"Le module {code} ne peut pas être mis à jour depuis l'interface "
                "(module fourni avec GeoNature, ou dossier source introuvable)."
            )
        if not module.get("git") and not TAG_RE.match(ref):
            raise UpdateRequestError(
                f"Le module {code} n'est pas un clone git : indiquez un tag de version "
                "(ex. 1.2.3), pas une branche ni un commit."
            )
        env.update(
            {
                "MODULE_CODE": code,
                "MODULE_REF": ref,
                "MODULE_DIR": module["directory"],
                "MODULE_REPO": module["github_repo"] or "",
            }
        )
    elif action == ACTION_REF:
        if target not in REF_TARGETS:
            raise UpdateRequestError("Référentiel inconnu.")
        env["TARGET"] = target
    elif action == ACTION_TAXREF:
        _validate_taxref(target, env)
        env["TARGET"] = target
    return env


def _quote_env(value):
    value = str(value)
    if any(c in value for c in "\n\r\"'\\$`"):
        raise UpdateRequestError("Valeur interdite dans la demande.")
    return f'"{value}"'


def write_request(env):
    state_dir().mkdir(parents=True, exist_ok=True)
    tmp = request_file().with_suffix(".tmp")
    with open(tmp, "w") as f:
        for key, value in env.items():
            f.write(f"{key}={_quote_env(value)}\n")
    tmp.replace(request_file())


# --- Taxref -------------------------------------------------------------------
# La migration de Taxref se fait en deux temps, avec les commandes de TaxHub :
#   1. analyse : geonature taxref migrate-to-vNN import-taxref-vNN
#      (import dans des tables temporaires + rapport des conflits, Taxref en
#      service inchangé) ;
#   2. application : geonature taxref migrate-to-vNN apply-changes
#      (possible seulement après une analyse sans conflit bloquant).
TAXREF_TARGET_RE = re.compile(r"^(analyse|apply):(\d{2})(:keep)?$")
TAXREF_REPORT_RE = re.compile(r"^[a-z0-9_]{1,60}\.csv$")


def taxref_info():
    """Version de Taxref installée (None si la base n'est pas joignable)."""
    try:
        from apptax.taxonomie.repositories import TaxrefInfoRepository

        info = TaxrefInfoRepository.getTaxrefInfo()
    except Exception:
        return None
    version = info.get("taxref_version")
    return {
        "version": int(version.version) if version is not None else None,
        "update_date": version.update_date if version is not None else None,
        "taxref_count": info.get("taxref_count"),
        "status_count": info.get("status_count"),
        "enabled_status_count": info.get("enabled_status_count"),
    }


def taxref_available_migrations():
    """Numéros de version vers lesquels la version de TaxHub installée sait migrer."""
    try:
        from apptax.taxonomie.commands.taxref import taxref as taxref_cli
    except Exception:
        return []
    versions = []
    for name in taxref_cli.commands:
        match = re.match(r"^migrate[-_]to[-_]v(\d+)$", name)
        if match:
            versions.append(int(match.group(1)))
    return sorted(versions)


def taxref_next_version(current=None):
    """Version suivante de Taxref si TaxHub sait y migrer, sinon None."""
    if current is None:
        info = taxref_info()
        current = info and info["version"]
    if not current:
        return None
    target = current + 1  # les migrations se font une version après l'autre
    return target if target in taxref_available_migrations() else None


def taxref_work_dir(version):
    return state_dir() / "taxref" / f"v{int(version)}"


def taxref_analysis(version):
    """Résultat de la dernière analyse pour cette version (écrit par le script)."""
    try:
        with open(state_dir() / "taxref" / f"analysis-v{int(version)}.json") as f:
            result = json.load(f)
    except (OSError, ValueError):
        return None
    return result if isinstance(result, dict) else None


def taxref_reports(version):
    """Fichiers CSV produits par l'analyse (liste_changements.csv, liste_split.csv…)."""
    report_dir = taxref_work_dir(version) / "tmp"
    if not report_dir.is_dir():
        return []
    return sorted(p.name for p in report_dir.iterdir() if TAXREF_REPORT_RE.match(p.name))


def _validate_taxref(target, env):
    match = TAXREF_TARGET_RE.match(target)
    if not match:
        raise UpdateRequestError("Demande Taxref invalide.")
    step, version, keep = match.group(1), int(match.group(2)), bool(match.group(3))
    if version != taxref_next_version():
        raise UpdateRequestError(
            f"La migration vers Taxref v{version} n'est pas disponible "
            "(elle se fait une version après l'autre, avec la version de TaxHub installée)."
        )
    if step == "apply":
        analysis = taxref_analysis(version)
        if not analysis or not analysis.get("ok"):
            raise UpdateRequestError(
                f"Lancez d'abord une analyse sans conflit bloquant pour Taxref v{version}."
            )
        if bool(analysis.get("keep_cdnom")) != keep:
            raise UpdateRequestError(
                "L'option « conserver les cd_nom disparus » doit être la même que lors de l'analyse."
            )
    env.update(
        {
            "TAXREF_STEP": step,
            "TAXREF_VERSION": str(version),
            "TAXREF_KEEP_CDNOM": "1" if keep else "0",
            "TAXREF_REGION": updater_config()["TAXREF_REGION"],
        }
    )
