import secrets
import time

from flask import abort, current_app, g, jsonify, redirect, request, session, url_for, flash
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
        return self.render(
            "admin/update.html",
            current_version=current,
            release=release,
            core_update_available=bool(release and utils.is_newer(release["version"], current)),
            modules=utils.list_modules(),
            ref_targets=utils.REF_TARGETS,
            status=utils.read_status(),
            can_update=self._can_action("U"),
            csrf_token=_csrf_token(),
        )

    @expose("/status")
    def status(self):
        return jsonify(status=utils.read_status(), log=utils.read_log_tail())

    @expose("/run", methods=["POST"])
    def run(self):
        if not self._can_action("U"):
            abort(403)
        token = request.form.get("csrf_token", "")
        if not secrets.compare_digest(token, session.get(CSRF_SESSION_KEY, "")):
            abort(400, "Jeton CSRF invalide.")
        action = request.form.get("action", "")
        target = request.form.get("target", "")
        if action == utils.ACTION_MODULE:
            target = f"{request.form.get('module_code', '')}:{target}"
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
