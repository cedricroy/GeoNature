from celery.schedules import crontab
from celery.utils.log import get_task_logger
from flask import current_app, render_template

from geonature.utils.celery import celery_app
from geonature.utils.config import config
from geonature.utils.utilsmails import send_mail

from . import reports

logger = get_task_logger(__name__)


@celery_app.on_after_finalize.connect
def setup_periodic_tasks(sender, **kwargs):
    cfg = config.get("EXPORT_TRACKING", {})
    if not (cfg.get("ENABLED") and cfg.get("MONTHLY_REPORT_ENABLED")):
        return
    minute, hour, day_of_month, month_of_year, day_of_week = cfg["MONTHLY_REPORT_CRONTAB"].split()
    sender.add_periodic_task(
        crontab(
            minute=minute,
            hour=hour,
            day_of_week=day_of_week,
            day_of_month=day_of_month,
            month_of_year=month_of_year,
        ),
        send_monthly_reports.s(),
        name="export tracking monthly reports",
    )


def build_reports(start, end):
    """Un rapport par organisme producteur ayant eu des téléchargements sur la période."""
    cfg = current_app.config["EXPORT_TRACKING"]
    rows = reports.report_rows(start, end, show_requester=cfg["SHOW_REQUESTER_ORGANISM"])
    by_producer = {}
    excluded = reports.excluded_organismes()
    for row in rows:
        if row["producer_id"] and row["producer_id"] not in excluded:
            by_producer.setdefault(row["producer_id"], []).append(row)
    result = []
    for organisme in reports.organismes(by_producer.keys()):
        producer_rows = by_producer[organisme["id_organisme"]]
        summary = reports.summary_by_producer(producer_rows)
        if not summary:  # uniquement des fiches de métadonnées
            continue
        result.append(
            {
                "organisme": organisme,
                "rows": producer_rows,
                "total": summary[0],
            }
        )
    return result


@celery_app.task(bind=True)
def send_monthly_reports(self, start=None, end=None):
    cfg = current_app.config["EXPORT_TRACKING"]
    if start is None or end is None:
        start, end = reports.previous_month_bounds()
    sent = 0
    for report in build_reports(start, end):
        recipients = []
        if report["organisme"]["email_organisme"]:
            recipients.append(report["organisme"]["email_organisme"])
        recipients += cfg["REPORT_CC"]
        if not recipients:
            logger.warning(
                "Pas d'adresse mail pour l'organisme %s : rapport non envoyé.",
                report["organisme"]["nom_organisme"],
            )
            continue
        html = render_template(
            "export_tracking/mail_report.html",
            start=start,
            end=end,
            url_page=current_app.config["API_ENDPOINT"] + "/export_tracking/mes-donnees",
            **report,
        )
        try:
            send_mail(
                recipients,
                f"Téléchargements de vos données naturalistes – {start.strftime('%m/%Y')}",
                html,
            )
        except Exception:
            # Adresse invalide, serveur SMTP indisponible… : on passe à l'organisme suivant.
            logger.exception(
                "Rapport non envoyé à l'organisme %s", report["organisme"]["nom_organisme"]
            )
            continue
        sent += 1
    logger.info("Suivi des exports : %s rapport(s) envoyé(s).", sent)
    return sent
