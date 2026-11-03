from flask import abort, current_app, request
from flask_admin import BaseView, expose
from utils_flask_sqla.response import to_csv_resp

from geonature.core.admin.utils import CruvedProtectedMixin

from . import reports
from .routes import parse_period
from .tracking import SOURCE_LABELS


class ExportTrackingAdminView(CruvedProtectedMixin, BaseView):
    module_code = "ADMIN"
    object_code = "ALL"

    def is_visible(self):
        return current_app.config["EXPORT_TRACKING"]["ENABLED"]

    def is_accessible(self):
        if not current_app.config["EXPORT_TRACKING"]["ENABLED"]:
            return False
        return super().is_accessible()

    def _filters(self):
        start, end = parse_period(request.args)

        def as_int(name):
            value = request.args.get(name, "")
            if not value:
                return None
            if not value.isdigit():
                abort(400)
            return int(value)

        source = request.args.get("source") or None
        if source is not None and source not in SOURCE_LABELS:
            abort(400)
        return {
            "start": start,
            "end": end,
            "producer": as_int("producer"),
            "requester": as_int("requester"),
            "source": source,
        }

    @expose("/")
    def index(self):
        filters = self._filters()
        rows = reports.report_rows(**filters)
        return self.render(
            "admin/export_tracking.html",
            filters=filters,
            rows=reports.merge_producers(rows),
            summary=reports.summary_by_producer(rows),
            unattributed=reports.unattributed_exports(filters["start"], filters["end"]),
            organismes=reports.organismes(),
            sources=SOURCE_LABELS,
            roles_label=reports.producer_roles_label(),
            query_string=request.query_string.decode(),
        )

    @expose("/export.csv")
    def csv(self):
        filters = self._filters()
        rows = reports.merge_producers(reports.report_rows(**filters))
        return to_csv_resp(
            f"suivi_exports_{filters['start'].isoformat()}_{filters['end'].isoformat()}",
            data=reports.csv_rows(rows),
            separator=";",
            columns=reports.CSV_COLUMNS,
        )
