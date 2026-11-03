import secrets
import time

from flask import (
    abort,
    current_app,
    flash,
    g,
    jsonify,
    redirect,
    request,
    send_from_directory,
    session,
    url_for,
)
from flask_admin import BaseView, expose

from geonature.core.admin.utils import CruvedProtectedMixin

from . import utils
from .tasks import launch_update

CSRF_SESSION_KEY = "gn_update_csrf"


def _csrf_token():
    token = session.get(CSRF_SESSION_KEY)
    if not token:
        token = secrets.token_urlsafe(32)
        session[CSRF_SESSION_KEY] = token
    return token


class UpdateAdminView(CruvedProtectedMixin, BaseView):
    module_code = "ADMIN"
    object_code = "ALL"

    def is_visible(self):
        return current_app.config["UPDATER"]["ENABLED"]

    def is_accessible(self):
        if not current_app.config["UPDATER"]["ENABLED"]:
            return False
        return super().is_accessible()

    @expose("/")
    def index(self):
        current = utils.installed_version()
        release = utils.latest_release()
        taxref = utils.taxref_info()
        taxref_next = utils.taxref_next_version(taxref["version"] if taxref else None)
        return self.render(
            "admin/update.html",
            current_version=current,
            release=release,
            core_update_available=bool(release and utils.is_newer(release["version"], current)),
            allow_core_update=current_app.config["UPDATER"]["ALLOW_CORE_UPDATE"],
            modules=utils.list_modules(),
            ref_targets=utils.REF_TARGETS,
            status=utils.read_status(),
            can_update=self._can_action("U"),
            csrf_token=_csrf_token(),
            taxref=taxref,
            taxref_next=taxref_next,
            taxref_analysis=utils.taxref_analysis(taxref_next) if taxref_next else None,
            taxref_reports=utils.taxref_reports(taxref_next) if taxref_next else [],
        )

    @expose("/taxref-report/<int:version>/<filename>")
    def taxref_report(self, version, filename):
        """Télécharger un rapport CSV de l'analyse Taxref."""
        if not utils.TAXREF_REPORT_RE.match(filename):
            abort(404)
        return send_from_directory(
            utils.taxref_work_dir(version) / "tmp",
            filename,
            as_attachment=True,
            mimetype="text/csv",
        )

    @expose("/status")
    def status(self):
        return jsonify(status=utils.read_status(), log=utils.read_log_tail())

    @expose("/run", methods=["POST"])
    def run(self):
        if not self._can_action("U"):
            abort(403)
        token = request.form.get("csrf_token", "")
        expected = session.get(CSRF_SESSION_KEY, "")
        # Un jeton absent d'un côté ou de l'autre est refusé (sinon "" == "" passerait).
        if not expected or not token or not secrets.compare_digest(token, expected):
            abort(400, "Jeton CSRF invalide.")
        action = request.form.get("action", "")
        target = request.form.get("target", "")
        if action == utils.ACTION_MODULE:
            target = f"{request.form.get('module_code', '')}:{target}"
        elif action == utils.ACTION_TAXREF and request.form.get("keep_cdnom"):
            target = f"{target}:keep"
        try:
            # Validation immédiate pour afficher l'erreur ; la tâche revalide.
            utils.validate_request(action, target)
        except utils.UpdateRequestError as exc:
            flash(str(exc), "error")
            return redirect(url_for(".index"))
        if utils.is_busy():
            flash("Une mise à jour est déjà en cours.", "error")
            return redirect(url_for(".index"))
        utils.write_status(state="queued", queued_ts=time.time(), action=action, target=target)
        launch_update.delay(action, target, g.current_user.id_role)
        flash("Mise à jour demandée. Suivez son avancement dans le journal ci-dessous.", "info")
        return redirect(url_for(".index"))
