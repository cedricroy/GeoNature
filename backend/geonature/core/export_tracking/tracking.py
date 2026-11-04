"""Enregistrement des téléchargements. Ne fait jamais échouer un export."""

import datetime
import hashlib
import logging
import threading
import time
from collections import Counter, OrderedDict

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

# Cache jeton -> demandeur : taille bornée, entrées expirées, clé = hash du jeton
_token_cache = OrderedDict()
TOKEN_CACHE_SECONDS = 600
TOKEN_CACHE_MAX_SIZE = 1000
_token_cache_lock = threading.Lock()


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


def _request_token():
    token = request.args.get("token") or request.headers.get("api-key")
    if not token:
        authorization = request.headers.get("Authorization", "")
        if authorization.lower().startswith("bearer "):
            token = authorization[7:].strip()
    return token or None


def _cache_get(key):
    with _token_cache_lock:
        entry = _token_cache.get(key)
        if entry is None:
            return None
        if time.monotonic() - entry[0] >= TOKEN_CACHE_SECONDS:
            _token_cache.pop(key, None)
            return None
        _token_cache.move_to_end(key)
        return entry[1]


def _cache_set(key, value):
    with _token_cache_lock:
        _token_cache[key] = (time.monotonic(), value)
        _token_cache.move_to_end(key)
        while len(_token_cache) > TOKEN_CACHE_MAX_SIZE:
            _token_cache.popitem(last=False)


def requester_from_request(id_export=None):
    """Organisme du demandeur : utilisateur connecté, jeton du module Export, ou public."""
    try:
        if current_user.is_authenticated:
            return _requester(getattr(current_user, "id_organisme", None))
    except Exception:
        pass
    token = _request_token()
    if token and id_export is not None:
        key = (id_export, hashlib.sha256(token.encode()).hexdigest())
        cached = _cache_get(key)
        if cached is not None:
            return cached
        try:
            with db.session.begin_nested():
                id_role = db.session.execute(
                    sa.text(
                        "SELECT id_role FROM gn_exports.cor_exports_roles "
                        "WHERE id_export = :id_export AND token = :token LIMIT 1"
                    ),
                    {"id_export": id_export, "token": token},
                ).scalar()
                result = requester_from_role(id_role) if id_role else (REQUESTER_PUBLIC, None)
        except Exception:
            log.warning("Suivi des exports : jeton non résolu", exc_info=True)
            result = (REQUESTER_PUBLIC, None)
        _cache_set(key, result)
        return result
    return REQUESTER_PUBLIC, None


# --- Décompte par jeu de données ---------------------------------------------------


def count_by_dataset_from_synthese(column, values):
    """
    Compte des observations par jeu de données à partir d'id_synthese ou d'unique_id_sinp.
    Les valeurs vides ou introuvables sont comptées en « jeu de données inconnu ».
    """
    values = list(values)
    nb_empty = sum(1 for v in values if v is None)
    values = [v for v in values if v is not None]
    if not values:
        return Counter({UNKNOWN_DATASET: nb_empty}) if nb_empty else Counter()
    if column == "id_synthese":
        query = sa.text(
            "SELECT id_synthese::text, id_dataset FROM gn_synthese.synthese "
            "WHERE id_synthese = ANY(CAST(:values AS integer[]))"
        )
    elif column == "unique_id_sinp":
        query = sa.text(
            "SELECT unique_id_sinp::text, id_dataset FROM gn_synthese.synthese "
            "WHERE unique_id_sinp = ANY(CAST(:values AS uuid[]))"
        )
    else:
        raise ValueError(column)
    # Correspondance valeur -> jeu de données (valeurs distinctes), puis décompte ligne
    # par ligne : une valeur répétée compte autant de fois qu'elle a été exportée.
    distinct_values = list({str(v) for v in values})
    dataset_of = {}
    for start in range(0, len(distinct_values), 10000):
        chunk = distinct_values[start : start + 10000]
        for value, id_dataset in db.session.execute(query, {"values": chunk}):
            dataset_of.setdefault(value.lower(), id_dataset)
    counts = Counter(dataset_of.get(str(v).lower()) or UNKNOWN_DATASET for v in values)
    if nb_empty:
        counts[UNKNOWN_DATASET] += nb_empty
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


def _guarded_counts(count_function, *args):
    """
    Exécute un décompte dans un point de sauvegarde : une erreur SQL (délai dépassé…)
    annule seulement ce point de sauvegarde et renvoie None, sans toucher à l'export.
    """
    try:
        with db.session.begin_nested():
            return count_function(*args)
    except Exception:
        log.exception("Suivi des exports : décompte impossible")
        return None


def _track(source, count_function, args, export_format=None, requester=None):
    """Décompte + enregistrement entièrement protégés : ne lève jamais d'exception."""
    if not is_enabled():
        return
    try:
        counts = _guarded_counts(count_function, *args)
        if counts is None:
            return
        if requester is None:
            requester = requester_from_request()
        record_export(source, counts, requester, export_format=export_format)
    except Exception:
        log.exception("Suivi des exports : enregistrement impossible")


def track_module_export(source, rows, export_format):
    """Exports des modules (Occtax, Occhab…) : lignes SQLAlchemy de la vue d'export."""
    _track(source, count_by_dataset_from_result, (rows,), export_format)


def track_synthese_export(rows, export_format):
    """Export d'observations de la Synthèse : lignes contenant id_synthese."""
    _track(
        SOURCE_SYNTHESE_OBSERVATIONS,
        lambda rs: count_by_dataset_from_synthese(
            "id_synthese", [getattr(r, "id_synthese", None) for r in rs]
        ),
        (rows,),
        export_format,
    )


def track_metadata_export(dataset_ids):
    """Export des métadonnées de la Synthèse : fiches de jeux de données, sans observations."""
    _track(
        SOURCE_SYNTHESE_METADATA,
        lambda ids: dict.fromkeys((int(i) for i in ids if i), 0),
        (dataset_ids,),
        "csv",
    )


# --- Enregistrement ------------------------------------------------------------------


def record_export(
    source,
    dataset_counts,
    requester,
    export_format=None,
    id_export=None,
    export_label=None,
    day=None,
    commit=True,
):
    """
    Ajoute un téléchargement aux compteurs du jour, dans un point de sauvegarde
    de la session : une erreur est journalisée, annule seulement ce point de
    sauvegarde, et n'interrompt jamais l'export en cours. La transaction est
    ensuite validée (commit=False : laissée à l'appelant).
    """
    if not is_enabled():
        return
    if not dataset_counts:
        return  # export vide : rien n'a été téléchargé
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
        # Une erreur ici annule seulement ce point de sauvegarde, pas la transaction
        # de la requête en cours.
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
                        nb_requests=1,
                    )
                    .on_conflict_do_update(
                        index_elements=["id_tracking", "id_dataset"],
                        set_={
                            "nb_observations": dataset_table.c.nb_observations + int(nb),
                            "nb_requests": dataset_table.c.nb_requests + 1,
                        },
                    )
                )
                db.session.execute(stmt)
    except Exception:
        log.exception("Suivi des exports : enregistrement impossible")
        return
    if commit:
        try:
            db.session.commit()
        except Exception:
            log.exception("Suivi des exports : validation impossible")
            db.session.rollback()
