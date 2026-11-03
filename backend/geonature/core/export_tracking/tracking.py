"""Enregistrement des téléchargements. Ne fait jamais échouer un export."""

import datetime
import logging
import time
from collections import Counter

import sqlalchemy as sa
from flask import current_app, request
from flask_login import current_user
from sqlalchemy.dialects.postgresql import insert

from geonature.utils.env import db

from .models import CorExportTrackingDataset, TExportTracking

log = logging.getLogger(__name__)

SOURCE_SYNTHESE_OBSERVATIONS = "synthese_observations"
SOURCE_SYNTHESE_METADATA = "synthese_metadata"
SOURCE_EXPORT_API = "export_api"
SOURCE_EXPORT_FILE = "export_file"
SOURCE_OCCTAX = "occtax"
SOURCE_OCCHAB = "occhab"

SOURCE_LABELS = {
    SOURCE_SYNTHESE_OBSERVATIONS: "Synthèse : export d'observations",
    SOURCE_SYNTHESE_METADATA: "Synthèse : export des métadonnées",
    SOURCE_EXPORT_API: "Module Export : API",
    SOURCE_EXPORT_FILE: "Module Export : fichier",
    SOURCE_OCCTAX: "Occtax : export",
    SOURCE_OCCHAB: "Occhab : export des stations",
}

REQUESTER_ORGANISME = "organisme"  # demandeur connu, avec un organisme
REQUESTER_INCONNU = "inconnu"  # demandeur connu (connecté ou jeton), sans organisme
REQUESTER_PUBLIC = "public"  # téléchargement public anonyme

UNKNOWN_DATASET = 0

_token_cache = {}
TOKEN_CACHE_SECONDS = 600


def is_enabled():
    try:
        return current_app.config["EXPORT_TRACKING"]["ENABLED"]
    except (KeyError, RuntimeError):
        return False


# --- Demandeur -------------------------------------------------------------------


def _organisme_of_role(id_role):
    if id_role is None:
        return None
    return db.session.execute(
        sa.text("SELECT id_organisme FROM utilisateurs.t_roles WHERE id_role = :id_role"),
        {"id_role": id_role},
    ).scalar()


def _requester(id_organisme):
    if id_organisme:
        return REQUESTER_ORGANISME, id_organisme
    return REQUESTER_INCONNU, None


def requester_from_role(id_role):
    if id_role is None:
        return REQUESTER_PUBLIC, None
    return _requester(_organisme_of_role(id_role))


def requester_from_request(id_export=None):
    """Organisme du demandeur : utilisateur connecté, jeton du module Export, ou public."""
    try:
        if current_user.is_authenticated:
            return _requester(getattr(current_user, "id_organisme", None))
    except Exception:
        pass
    token = request.args.get("token") or request.headers.get("api-key")
    if token and id_export is not None:
        key = (id_export, token)
        cached = _token_cache.get(key)
        if cached and time.monotonic() - cached[0] < TOKEN_CACHE_SECONDS:
            return cached[1]
        try:
            id_role = db.session.execute(
                sa.text(
                    "SELECT id_role FROM gn_exports.cor_exports_roles "
                    "WHERE id_export = :id_export AND token = :token LIMIT 1"
                ),
                {"id_export": id_export, "token": token},
            ).scalar()
        except Exception:
            db.session.rollback()
            id_role = None
        result = requester_from_role(id_role) if id_role else (REQUESTER_PUBLIC, None)
        _token_cache[key] = (time.monotonic(), result)
        return result
    return REQUESTER_PUBLIC, None


# --- Décompte par jeu de données ---------------------------------------------------


def count_by_dataset_from_synthese(column, values):
    """Compte des observations par jeu de données à partir d'id_synthese ou d'unique_id_sinp."""
    values = [v for v in values if v is not None]
    if not values:
        return Counter()
    if column == "id_synthese":
        query = sa.text(
            "SELECT id_dataset, count(*) FROM gn_synthese.synthese "
            "WHERE id_synthese = ANY(CAST(:values AS integer[])) GROUP BY id_dataset"
        )
    elif column == "unique_id_sinp":
        query = sa.text(
            "SELECT id_dataset, count(*) FROM gn_synthese.synthese "
            "WHERE unique_id_sinp = ANY(CAST(:values AS uuid[])) GROUP BY id_dataset"
        )
    else:
        raise ValueError(column)
    counts = Counter()
    for start in range(0, len(values), 10000):
        chunk = values[start : start + 10000]
        rows = db.session.execute(query, {"values": [str(v) for v in chunk]})
        for id_dataset, nb in rows:
            counts[id_dataset or UNKNOWN_DATASET] += nb
    found = sum(counts.values())
    if found < len(values):
        counts[UNKNOWN_DATASET] += len(values) - found
    return counts


def count_by_dataset_from_rows(rows):
    """
    Compte par jeu de données à partir de lignes exportées (dictionnaires).
    Utilise id_dataset, sinon id_synthese, sinon unique_id_sinp.
    """
    rows = list(rows)
    if not rows:
        return Counter()
    sample = rows[0]
    if "id_dataset" in sample:
        return Counter(r.get("id_dataset") or UNKNOWN_DATASET for r in rows)
    for column in ("id_synthese", "unique_id_sinp"):
        if column in sample:
            return count_by_dataset_from_synthese(column, [r.get(column) for r in rows])
    return Counter({UNKNOWN_DATASET: len(rows)})


def count_by_dataset_from_result(rows):
    """
    Compte par jeu de données à partir de lignes SQLAlchemy (Row) d'une vue d'export.
    Lit seulement la colonne id_dataset (ou id_synthese / unique_id_sinp), sans copier les lignes.
    """
    rows = list(rows)
    if not rows:
        return Counter()
    keys = set(rows[0]._mapping.keys())
    if "id_dataset" in keys:
        return Counter(r._mapping["id_dataset"] or UNKNOWN_DATASET for r in rows)
    for column in ("id_synthese", "unique_id_sinp"):
        if column in keys:
            return count_by_dataset_from_synthese(column, [r._mapping[column] for r in rows])
    return Counter({UNKNOWN_DATASET: len(rows)})


def track_module_export(source, rows, export_format):
    """Raccourci pour les exports des modules (Occtax, Occhab…)."""
    if not is_enabled():
        return
    try:
        counts = count_by_dataset_from_result(rows)
    except Exception:
        log.exception("Suivi des exports : décompte impossible")
        db.session.rollback()
        return
    record_export(source, counts, requester_from_request(), export_format=export_format)


# --- Enregistrement ------------------------------------------------------------------


def record_export(
    source,
    dataset_counts,
    requester,
    export_format=None,
    id_export=None,
    export_label=None,
    day=None,
):
    """
    Ajoute un téléchargement aux compteurs du jour, dans un point de sauvegarde
    de la session : une erreur est journalisée, annule seulement ce point de
    sauvegarde, et n'interrompt jamais l'export en cours.
    """
    if not is_enabled():
        return
    requester_type, id_organisme = requester
    day = day or datetime.date.today()
    key = "|".join(
        str(x)
        for x in (
            day.isoformat(),
            source,
            id_export or "",
            export_format or "",
            requester_type,
            id_organisme or "",
        )
    )
    tracking_table = TExportTracking.__table__
    dataset_table = CorExportTrackingDataset.__table__
    try:
        with db.session.begin_nested():
            stmt = (
                insert(tracking_table)
                .values(
                    tracking_key=key,
                    day=day,
                    source=source,
                    id_export=id_export,
                    export_label=(export_label or "")[:255] or None,
                    export_format=export_format,
                    requester_type=requester_type,
                    requester_id_organisme=id_organisme,
                    nb_requests=1,
                )
                .on_conflict_do_update(
                    index_elements=["tracking_key"],
                    set_={"nb_requests": tracking_table.c.nb_requests + 1},
                )
                .returning(tracking_table.c.id_tracking)
            )
            id_tracking = db.session.execute(stmt).scalar()
            for id_dataset, nb in dict(dataset_counts).items():
                stmt = (
                    insert(dataset_table)
                    .values(
                        id_tracking=id_tracking,
                        id_dataset=int(id_dataset or UNKNOWN_DATASET),
                        nb_observations=int(nb),
                    )
                    .on_conflict_do_update(
                        index_elements=["id_tracking", "id_dataset"],
                        set_={"nb_observations": dataset_table.c.nb_observations + int(nb)},
                    )
                )
                db.session.execute(stmt)
        db.session.commit()
    except Exception:
        log.exception("Suivi des exports : enregistrement impossible")
        try:
            db.session.rollback()
        except Exception:
            pass
