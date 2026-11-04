"""Lecture du suivi : qui a téléchargé quelles données de quel organisme producteur."""

import datetime
from collections import OrderedDict, defaultdict

import sqlalchemy as sa
from flask import current_app

from geonature.utils.env import db

from .tracking import (
    REQUESTER_INCONNU,
    REQUESTER_PUBLIC,
    SOURCE_LABELS,
    SOURCE_SYNTHESE_METADATA,
)

# Rôles d'acteur (nomenclature ROLE_ACTEUR) les plus courants, pour l'affichage.
ACTOR_ROLE_LABELS = {
    "1": "Contact principal",
    "2": "Financeur",
    "3": "Maître d'ouvrage",
    "4": "Maître d'œuvre",
    "5": "Fournisseur",
    "6": "Producteur",
    "7": "Point de contact base de données de production",
    "8": "Point de contact pour les métadonnées",
}

_PRODUCERS_CTE = """
WITH roles AS (
    SELECT n.id_nomenclature
    FROM ref_nomenclatures.t_nomenclatures n
    JOIN ref_nomenclatures.bib_nomenclatures_types t USING (id_type)
    WHERE t.mnemonique = 'ROLE_ACTEUR'
      AND (:all_roles OR n.cd_nomenclature = ANY(CAST(:roles AS text[])))
),
dataset_producers AS (
    SELECT cda.id_dataset, COALESCE(cda.id_organism, r.id_organisme) AS id_organisme
    FROM gn_meta.cor_dataset_actor cda
    LEFT JOIN utilisateurs.t_roles r ON r.id_role = cda.id_role
    WHERE cda.id_nomenclature_actor_role IN (SELECT id_nomenclature FROM roles)
),
af_producers AS (
    SELECT d.id_dataset, COALESCE(caa.id_organism, r.id_organisme) AS id_organisme
    FROM gn_meta.t_datasets d
    JOIN gn_meta.cor_acquisition_framework_actor caa
      ON caa.id_acquisition_framework = d.id_acquisition_framework
    LEFT JOIN utilisateurs.t_roles r ON r.id_role = caa.id_role
    WHERE :use_af
      AND caa.id_nomenclature_actor_role IN (SELECT id_nomenclature FROM roles)
      AND NOT EXISTS (
          SELECT 1 FROM dataset_producers dp
          WHERE dp.id_dataset = d.id_dataset AND dp.id_organisme IS NOT NULL
      )
),
producers AS (
    SELECT DISTINCT id_dataset, id_organisme FROM dataset_producers WHERE id_organisme IS NOT NULL
    UNION
    SELECT DISTINCT id_dataset, id_organisme FROM af_producers WHERE id_organisme IS NOT NULL
)
"""

_REPORT_QUERY = (
    _PRODUCERS_CTE
    + """
SELECT t.id_tracking, t.day, t.source, t.id_export, t.export_label, t.export_format,
       t.requester_type, t.requester_id_organisme, ro.nom_organisme AS requester_name,
       c.nb_requests, c.id_dataset, d.dataset_name,
       p.id_organisme AS producer_id, po.nom_organisme AS producer_name,
       c.nb_observations
FROM gn_commons.t_export_tracking t
JOIN gn_commons.cor_export_tracking_dataset c ON c.id_tracking = t.id_tracking
LEFT JOIN gn_meta.t_datasets d ON d.id_dataset = c.id_dataset
LEFT JOIN producers p ON p.id_dataset = c.id_dataset
LEFT JOIN utilisateurs.bib_organismes po ON po.id_organisme = p.id_organisme
LEFT JOIN utilisateurs.bib_organismes ro ON ro.id_organisme = t.requester_id_organisme
WHERE t.day BETWEEN :start AND :end
  AND (CAST(:producer AS integer) IS NULL OR p.id_organisme = :producer)
  AND (CAST(:requester AS integer) IS NULL OR t.requester_id_organisme = :requester)
  AND (CAST(:source AS text) IS NULL OR t.source = :source)
ORDER BY t.day DESC, po.nom_organisme NULLS LAST, d.dataset_name NULLS LAST
"""
)

CSV_COLUMNS = [
    "date",
    "organisme_producteur",
    "jeu_de_donnees",
    "id_dataset",
    "source",
    "export",
    "format",
    "telecharge_par",
    "nb_telechargements",
    "nb_observations",
]


def tracking_config():
    return current_app.config["EXPORT_TRACKING"]


def _role_params():
    roles = [str(r) for r in tracking_config()["PRODUCER_ACTOR_ROLES"]]
    return {
        "all_roles": "*" in roles,
        "roles": [r for r in roles if r != "*"],
        "use_af": tracking_config()["USE_ACQUISITION_FRAMEWORK_ACTORS"],
    }


def producer_roles_label():
    roles = [str(r) for r in tracking_config()["PRODUCER_ACTOR_ROLES"]]
    if "*" in roles:
        return "tous les rôles d'acteur"
    return ", ".join(ACTOR_ROLE_LABELS.get(r, f"rôle {r}") for r in roles)


def requester_label(row, show_requester=True):
    if row["requester_type"] == REQUESTER_PUBLIC:
        return "Téléchargement public (anonyme)"
    if not show_requester:
        return "Demandeur identifié"
    if row["requester_type"] == REQUESTER_INCONNU or not row["requester_name"]:
        return "Demandeur sans organisme"
    return row["requester_name"]


def export_label(row):
    label = SOURCE_LABELS.get(row["source"], row["source"])
    if row["export_label"]:
        label = f"{label} « {row['export_label']} »"
    return label


def month_bounds(day=None):
    day = day or datetime.date.today()
    start = day.replace(day=1)
    end = (start + datetime.timedelta(days=32)).replace(day=1) - datetime.timedelta(days=1)
    return start, end


def previous_month_bounds(day=None):
    day = day or datetime.date.today()
    return month_bounds(day.replace(day=1) - datetime.timedelta(days=1))


def report_rows(start, end, producer=None, requester=None, source=None, show_requester=True):
    params = dict(
        _role_params(),
        start=start,
        end=end,
        producer=producer,
        requester=requester,
        source=source,
    )
    rows = []
    for r in db.session.execute(sa.text(_REPORT_QUERY), params).mappings():
        row = dict(r)
        row["export"] = export_label(row)
        row["requester"] = requester_label(row, show_requester)
        row["dataset"] = row["dataset_name"] or (
            "Jeu de données inconnu" if not row["id_dataset"] else "Jeu de données supprimé"
        )
        row["producer"] = row["producer_name"] or "Producteur non renseigné"
        rows.append(row)
    return rows


def merge_producers(rows):
    """
    Une ligne par téléchargement et jeu de données, avec les producteurs réunis.
    (La requête renvoie une ligne par producteur : sans regroupement, un jeu de
    données à plusieurs producteurs serait compté plusieurs fois dans le détail.)
    """
    merged = OrderedDict()
    for r in rows:
        key = (r["id_tracking"], r["id_dataset"])
        if key not in merged:
            merged[key] = dict(r, producer_ids=[], producers=[])
        m = merged[key]
        if r["producer_id"] and r["producer_id"] not in m["producer_ids"]:
            m["producer_ids"].append(r["producer_id"])
            m["producers"].append(r["producer"])
    for m in merged.values():
        m["producer"] = ", ".join(m["producers"]) or "Producteur non renseigné"
    return list(merged.values())


_CSV_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def csv_safe(value):
    """Neutralise les formules (=, +, -, @…) dans les cellules CSV ouvertes dans un tableur."""
    if isinstance(value, str) and value.startswith(_CSV_FORMULA_PREFIXES):
        return "'" + value
    return value


def csv_rows(rows):
    return [
        {
            key: csv_safe(value)
            for key, value in {
                "date": r["day"].isoformat(),
                "organisme_producteur": r["producer"],
                "jeu_de_donnees": r["dataset"],
                "id_dataset": r["id_dataset"] or "",
                "source": SOURCE_LABELS.get(r["source"], r["source"]),
                "export": r["export_label"] or "",
                "format": r["export_format"] or "",
                "telecharge_par": r["requester"],
                "nb_telechargements": r["nb_requests"],
                "nb_observations": r["nb_observations"],
            }.items()
        }
        for r in rows
    ]


def excluded_organismes():
    return {int(i) for i in tracking_config()["EXCLUDED_ORGANISMS"]}


def summary_by_producer(rows):
    """Totaux par organisme producteur : observations téléchargées, et par demandeur."""
    summary = OrderedDict()
    for r in rows:
        if r["source"] == SOURCE_SYNTHESE_METADATA:
            continue  # fiches de métadonnées : pas d'observations, pas dans les totaux
        key = r["producer_id"] or 0
        s = summary.setdefault(
            key,
            {
                "producer": r["producer"],
                "producer_id": r["producer_id"],
                "nb_observations": 0,
                "datasets": set(),
                "requesters": defaultdict(int),
            },
        )
        s["nb_observations"] += r["nb_observations"]
        s["datasets"].add(r["id_dataset"])
        s["requesters"][r["requester"]] += r["nb_observations"]
    result = sorted(summary.values(), key=lambda s: -s["nb_observations"])
    for s in result:
        s["nb_datasets"] = len(s["datasets"])
        s["requesters"] = sorted(s["requesters"].items(), key=lambda kv: -kv[1])
    return result


def unattributed_exports(start, end, requester=None, source=None):
    """Exports du module Export dont la vue ne permet pas de retrouver le jeu de données."""
    return (
        db.session.execute(
            sa.text(
                """
            SELECT t.id_export, max(t.export_label) AS export_label,
                   sum(c.nb_observations) AS nb_observations
            FROM gn_commons.t_export_tracking t
            JOIN gn_commons.cor_export_tracking_dataset c ON c.id_tracking = t.id_tracking
            WHERE c.id_dataset = 0 AND t.day BETWEEN :start AND :end
              AND t.source IN ('export_api', 'export_file')
              AND (CAST(:requester AS integer) IS NULL OR t.requester_id_organisme = :requester)
              AND (CAST(:source AS text) IS NULL OR t.source = :source)
            GROUP BY t.id_export
            """
            ),
            {"start": start, "end": end, "requester": requester, "source": source},
        )
        .mappings()
        .all()
    )


def organismes(ids=None):
    query = "SELECT id_organisme, nom_organisme, email_organisme FROM utilisateurs.bib_organismes"
    params = {}
    if ids is not None:
        query += " WHERE id_organisme = ANY(CAST(:ids AS integer[]))"
        params["ids"] = list(ids)
    return db.session.execute(sa.text(query + " ORDER BY nom_organisme"), params).mappings().all()
