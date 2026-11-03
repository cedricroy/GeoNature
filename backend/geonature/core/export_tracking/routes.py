import datetime

from flask import Blueprint, abort, current_app, render_template, request
from flask_login import current_user
from utils_flask_sqla.response import to_csv_resp

from . import reports
from . import tasks  # noqa: F401  (enregistrement des tâches Celery)

routes = Blueprint("export_tracking", __name__)


def parse_period(args):
    """Période demandée (?start=AAAA-MM-JJ&end=AAAA-MM-JJ), mois en cours par défaut."""
    default_start, default_end = reports.month_bounds()
    try:
        start = datetime.date.fromisoformat(args.get("start", "")) if args.get("start") else None
        end = datetime.date.fromisoformat(args.get("end", "")) if args.get("end") else None
    except ValueError:
        abort(400, "Date invalide (format attendu : AAAA-MM-JJ).")
    start, end = start or default_start, end or default_end
    if start > end:
        start, end = end, start
    return start, end


def _current_organisme():
    if not current_user.is_authenticated:
        abort(401)
    return getattr(current_user, "id_organisme", None)


def _my_rows():
    if not current_app.config["EXPORT_TRACKING"]["ENABLED"]:
        abort(404)
    id_organisme = _current_organisme()
    start, end = parse_period(request.args)
    rows = []
    if id_organisme:
        rows = reports.report_rows(
            start,
            end,
            producer=id_organisme,
            show_requester=current_app.config["EXPORT_TRACKING"]["SHOW_REQUESTER_ORGANISM"],
        )
    return id_organisme, start, end, rows


@routes.route("/mes-donnees", methods=["GET"])
def my_data():
    """Téléchargements des données dont l'organisme de l'utilisateur est producteur."""
    id_organisme, start, end, rows = _my_rows()
    organisme = reports.organismes([id_organisme])[0] if id_organisme else None
    summary = reports.summary_by_producer(rows)
    return render_template(
        "export_tracking/mes_donnees.html",
        organisme=organisme,
        start=start,
        end=end,
        rows=rows,
        total=summary[0] if summary else None,
        roles_label=reports.producer_roles_label(),
        url_application=current_app.config["URL_APPLICATION"],
    )


@routes.route("/mes-donnees.csv", methods=["GET"])
def my_data_csv():
    _, start, end, rows = _my_rows()
    return to_csv_resp(
        f"telechargements_{start.isoformat()}_{end.isoformat()}",
        data=reports.csv_rows(rows),
        separator=";",
        columns=reports.CSV_COLUMNS,
    )
