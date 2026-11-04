"""
Branchement du suivi sur le module Export (gn_module_export), sans le modifier :
- API (/exports/api/<id>) : lecture de la réponse JSON dans after_request ;
- fichiers (tâche Celery generate_export) : signal task_postrun, puis décompte
  par jeu de données sur la vue de l'export (le module exporte la vue entière).
"""

import logging
import os
import re

import sqlalchemy as sa
from celery.signals import task_postrun
from flask import has_app_context, request

from geonature.utils.env import db

from . import tracking

log = logging.getLogger(__name__)

EXPORT_API_ENDPOINTS = {"exports.get_one_export_api"}
GENERATE_EXPORT_TASK_RE = re.compile(r"(^|\.)generate_export$")
IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _export_info(id_export):
    if id_export is None:
        return None
    try:
        with db.session.begin_nested():
            return (
                db.session.execute(
                    sa.text(
                        "SELECT id, label, schema_name, view_name FROM gn_exports.t_exports "
                        "WHERE id = :id"
                    ),
                    {"id": id_export},
                )
                .mappings()
                .first()
            )
    except Exception:
        log.warning("Suivi des exports : export %s introuvable", id_export, exc_info=True)
        return None


def _id_export_from_view_args():
    """id_export de l'URL ; à défaut, le premier paramètre entier de la route."""
    view_args = request.view_args or {}
    if isinstance(view_args.get("id_export"), int):
        return view_args["id_export"]
    for value in view_args.values():
        if isinstance(value, int):
            return value
    return None


def _rows_from_payload(payload):
    """Lignes d'une réponse de l'API du module Export (items, ou features GeoJSON)."""
    if not isinstance(payload, dict):
        return []
    if isinstance(payload.get("items"), list):
        items = payload["items"]
    elif isinstance(payload.get("features"), list):
        items = payload["features"]
    else:
        return []
    rows = []
    for item in items:
        if isinstance(item, dict) and isinstance(item.get("properties"), dict):
            item = item["properties"]
        if isinstance(item, dict):
            rows.append(item)
    return rows


def track_export_api_response(response):
    if request.endpoint not in EXPORT_API_ENDPOINTS or response.status_code != 200:
        return response
    if not tracking.is_enabled():
        return response
    try:
        id_export = _id_export_from_view_args()
        rows = _rows_from_payload(response.get_json(silent=True))
        if not rows:
            return response
        counts = tracking._guarded_counts(tracking.count_by_dataset_from_rows, rows)
        if counts is None:
            return response
        info = _export_info(id_export)
        tracking.record_export(
            tracking.SOURCE_EXPORT_API,
            counts,
            tracking.requester_from_request(id_export),
            export_format="json",
            id_export=id_export,
            export_label=info["label"] if info else None,
        )
    except Exception:
        log.exception("Suivi des exports : API du module Export")
    return response


def count_view_by_dataset(schema_name, view_name):
    """Décompte par jeu de données sur une vue d'export entière."""
    if not (IDENTIFIER_RE.match(schema_name or "") and IDENTIFIER_RE.match(view_name or "")):
        return None
    columns = set(
        db.session.execute(
            sa.text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = :schema AND table_name = :view"
            ),
            {"schema": schema_name, "view": view_name},
        ).scalars()
    )
    table = f'"{schema_name}"."{view_name}"'
    if "id_dataset" in columns:
        query = f"SELECT id_dataset, count(*) FROM {table} GROUP BY id_dataset"
    elif "id_synthese" in columns:
        query = (
            f"SELECT s.id_dataset, count(*) FROM {table} v "
            "LEFT JOIN gn_synthese.synthese s ON s.id_synthese = v.id_synthese "
            "GROUP BY s.id_dataset"
        )
    elif "unique_id_sinp" in columns:
        query = (
            f"SELECT s.id_dataset, count(*) FROM {table} v "
            "LEFT JOIN gn_synthese.synthese s ON s.unique_id_sinp = v.unique_id_sinp "
            "GROUP BY s.id_dataset"
        )
    else:
        query = f"SELECT 0, count(*) FROM {table}"
    return {
        (id_dataset or tracking.UNKNOWN_DATASET): nb
        for id_dataset, nb in db.session.execute(sa.text(query))
    }


def on_task_postrun(
    sender=None, task_id=None, task=None, args=None, kwargs=None, retval=None, state=None, **_
):
    name = getattr(task, "name", "") or getattr(sender, "name", "")
    if not GENERATE_EXPORT_TASK_RE.search(name) or state != "SUCCESS":
        return
    if isinstance(retval, BaseException):  # tâche terminée sur une erreur rattrapée
        return
    if has_app_context():
        # Mode eager (tâche exécutée dans la requête HTTP, utilisé en test) : on
        # enregistre dans la session de la requête, sans la fermer. record_export
        # valide alors la transaction de la requête du module Export, dont la route
        # ne fait que des lectures.
        _track_generated_file(args, kwargs, own_context=False)
        return
    # Mode worker : le signal est envoyé hors du contexte Flask de la tâche.
    from geonature.utils.celery import celery_app

    app = getattr(celery_app, "app", None)
    if app is None:
        return
    with app.app_context():
        _track_generated_file(args, kwargs, own_context=True)


def _track_generated_file(args, kwargs, own_context):
    if not tracking.is_enabled():
        return
    try:
        params = dict(kwargs or {})
        names = [
            "export_id",
            "file_name",
            "export_url",
            "format",
            "id_role",
            "filters",
            "schedule_id",
        ]
        for key, value in zip(names, args or ()):
            params.setdefault(key, value)
        # Export programmé (régénéré chaque nuit, sans demandeur) : ce n'est pas un
        # téléchargement, on ne le compte pas.
        if params.get("schedule_id") is not None:
            return
        # Garde-fou : une génération à la demande a toujours un demandeur connecté.
        if params.get("id_role") is None:
            return
        # Génération ratée sans exception (erreur rattrapée par le module) : pas de fichier.
        file_name = params.get("file_name")
        if (
            isinstance(file_name, str)
            and os.path.isabs(file_name)
            and not os.path.exists(file_name)
        ):
            log.info("Suivi des exports : fichier %s absent, export non compté", file_name)
            return
        info = _export_info(params.get("export_id"))
        if not info:
            return
        counts = tracking._guarded_counts(
            count_view_by_dataset, info["schema_name"], info["view_name"]
        )
        if counts is None:
            return
        tracking.record_export(
            tracking.SOURCE_EXPORT_FILE,
            counts,
            tracking.requester_from_role(params.get("id_role")),
            export_format=str(params.get("format") or "")[:20] or None,
            id_export=info["id"],
            export_label=info["label"],
        )
    except Exception:
        log.exception("Suivi des exports : fichier du module Export")
    finally:
        if own_context:
            db.session.remove()


def check_export_module(app):
    """Avertit si le module Export est installé mais que sa route d'API a changé de nom."""
    has_export_blueprint = any(endpoint.startswith("exports.") for endpoint in app.view_functions)
    if has_export_blueprint and not EXPORT_API_ENDPOINTS & set(app.view_functions):
        log.warning(
            "Suivi des exports : la route d'API du module Export (%s) est introuvable ; "
            "les téléchargements par l'API ne seront pas suivis.",
            ", ".join(sorted(EXPORT_API_ENDPOINTS)),
        )


def init_app(app):
    app.after_request(track_export_api_response)
    task_postrun.connect(on_task_postrun, weak=False)
