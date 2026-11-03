import subprocess
import time
from datetime import datetime, timezone

from celery.utils.log import get_task_logger

from geonature.utils.celery import celery_app

from . import utils

logger = get_task_logger(__name__)


def _now():
    return datetime.now(timezone.utc).isoformat()


@celery_app.task(bind=True)
def launch_update(self, action, target=None, id_role=None):
    """
    Valide la demande, l'écrit dans request.env et démarre geonature-update.service.

    La tâche ne réalise pas la mise à jour elle-même : le worker Celery est arrêté
    pendant une mise à jour de GeoNature ou d'un module, ce qui tuerait le
    processus. Le service systemd, lui, tourne indépendamment du worker.
    """
    base_status = {"action": action, "target": target, "requested_by": id_role}
    try:
        env = utils.validate_request(action, target)
    except utils.UpdateRequestError as exc:
        utils.write_status(state="failed", message=str(exc), finished_at=_now(), **base_status)
        return
    if utils.read_status().get("state") == "running":
        logger.warning("Une mise à jour est déjà en cours, demande ignorée.")
        return
    if id_role is not None:
        env["REQUESTED_BY"] = str(int(id_role))
    try:
        utils.write_request(env)
    except (utils.UpdateRequestError, OSError) as exc:
        utils.write_status(
            state="failed",
            message=f"Demande impossible à écrire : {exc}",
            finished_at=_now(),
            **base_status,
        )
        return
    # « queued » est écrit AVANT le démarrage du service : le script le remplace
    # ensuite par « running » / « failed » / « success » sans risque d'être écrasé.
    utils.write_status(state="queued", queued_at=_now(), queued_ts=time.time(), **base_status)
    service = utils.updater_config()["SERVICE_NAME"]
    cmd = ["sudo", "-n", "systemctl", "start", "--no-block", service]
    logger.info("Lancement de %s (%s %s)", service, action, target or "")
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
        result = None
        error = str(exc)
    else:
        error = (result.stderr or result.stdout).strip()
    if result is None or result.returncode != 0:
        utils.write_status(
            state="failed",
            message=(
                f"Impossible de démarrer {service} : {error or 'erreur inconnue'}. "
                "Vérifiez que l'unité systemd et la règle sudoers de l'outil de mise "
                "à jour sont installées."
            ),
            finished_at=_now(),
            **base_status,
        )
        return
