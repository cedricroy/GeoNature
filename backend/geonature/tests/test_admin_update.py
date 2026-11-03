import json
import subprocess
from unittest import mock

import pytest
from flask import current_app, url_for
from pypnusershub.tests.utils import set_logged_user

from geonature.core.admin.update import utils
from geonature.core.admin.update.tasks import launch_update
from geonature.core.admin.update.views import CSRF_SESSION_KEY


@pytest.fixture
def updater(monkeypatch, tmp_path):
    cfg = dict(current_app.config["UPDATER"])
    cfg.update(
        ENABLED=True,
        STATE_DIR=str(tmp_path / "state"),
        LOG_DIR=str(tmp_path / "log"),
    )
    monkeypatch.setitem(current_app.config, "UPDATER", cfg)
    monkeypatch.setattr(utils, "latest_release", lambda repo=None: None)
    return cfg


@pytest.fixture
def mock_systemctl():
    completed = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
    with mock.patch(
        "geonature.core.admin.update.tasks.subprocess.run", return_value=completed
    ) as run:
        yield run


def _read_request(cfg):
    with open(f"{cfg['STATE_DIR']}/request.env") as f:
        return dict(line.rstrip("\n").split("=", 1) for line in f)


@pytest.mark.usefixtures("client_class", "temporary_transaction")
class TestUpdateAdminView:
    def _set_csrf(self, token="token"):
        with self.client.session_transaction() as session:
            session[CSRF_SESSION_KEY] = token
        return token

    def test_disabled(self, users):
        set_logged_user(self.client, users["admin_user"])
        response = self.client.get(url_for("update.index"))
        assert response.status_code == 403

    def test_no_right(self, users, updater):
        set_logged_user(self.client, users["noright_user"])
        response = self.client.get(url_for("update.index"))
        assert response.status_code == 403

    def test_index(self, users, updater):
        set_logged_user(self.client, users["admin_user"])
        response = self.client.get(url_for("update.index"))
        assert response.status_code == 200
        assert "Mettre à jour la base" in response.text
        assert "Rebuild du frontend" in response.text

    def test_status(self, users, updater):
        set_logged_user(self.client, users["admin_user"])
        response = self.client.get(url_for("update.status"))
        assert response.status_code == 200
        assert response.json == {"status": {"state": "idle"}, "log": []}

        log_file = f"{updater['LOG_DIR']}/x.log"
        utils.write_status(state="running", log_file=log_file)
        utils.Path(updater["LOG_DIR"]).mkdir(parents=True)
        with open(log_file, "w") as f:
            f.write("ligne 1\nligne 2\n")
        response = self.client.get(url_for("update.status"))
        assert response.json["status"]["state"] == "running"
        assert response.json["log"] == ["ligne 1", "ligne 2"]

    def test_status_log_outside_log_dir(self, users, updater, tmp_path):
        set_logged_user(self.client, users["admin_user"])
        secret = tmp_path / "secret.txt"
        secret.write_text("secret")
        utils.write_status(state="failed", log_file=str(secret))
        response = self.client.get(url_for("update.status"))
        assert response.json["log"] == []

    def test_run_csrf(self, users, updater, mock_systemctl):
        set_logged_user(self.client, users["admin_user"])
        self._set_csrf("good")
        response = self.client.post(
            url_for("update.run"), data={"action": "db", "csrf_token": "bad"}
        )
        assert response.status_code == 400
        mock_systemctl.assert_not_called()

    def test_run_no_update_right(self, users, updater, mock_systemctl):
        set_logged_user(self.client, users["self_user"])
        token = self._set_csrf()
        response = self.client.post(
            url_for("update.run"), data={"action": "db", "csrf_token": token}
        )
        assert response.status_code == 403
        mock_systemctl.assert_not_called()

    @pytest.mark.parametrize(
        "action,target",
        [
            ("rm", ""),
            ("core", "1.0; rm -rf /"),
            ("core", "$(id)"),
            ("ref", "taxref"),
        ],
    )
    def test_run_invalid(self, users, updater, mock_systemctl, action, target):
        set_logged_user(self.client, users["admin_user"])
        token = self._set_csrf()
        response = self.client.post(
            url_for("update.run"),
            data={"action": action, "target": target, "csrf_token": token},
        )
        assert response.status_code == 302
        mock_systemctl.assert_not_called()
        assert not utils.request_file().exists()

    def test_run_db(self, users, updater, mock_systemctl, celery_eager):
        set_logged_user(self.client, users["admin_user"])
        token = self._set_csrf()
        response = self.client.post(
            url_for("update.run"), data={"action": "db", "csrf_token": token}
        )
        assert response.status_code == 302
        mock_systemctl.assert_called_once()
        assert mock_systemctl.call_args.args[0] == [
            "sudo",
            "-n",
            "systemctl",
            "start",
            "--no-block",
            "geonature-update.service",
        ]
        request = _read_request(updater)
        assert request["ACTION"] == '"db"'
        assert request["REQUESTED_BY"] == f'"{users["admin_user"].id_role}"'
        assert utils.read_status()["state"] == "queued"

    def test_run_busy(self, users, updater, mock_systemctl, celery_eager):
        set_logged_user(self.client, users["admin_user"])
        token = self._set_csrf()
        utils.write_status(state="running")
        self.client.post(url_for("update.run"), data={"action": "db", "csrf_token": token})
        mock_systemctl.assert_not_called()


class TestUpdateUtils:
    def test_validate_core(self, app, updater):
        env = utils.validate_request("core", "2.17.6")
        assert env["ACTION"] == "core"
        assert env["TARGET"] == "2.17.6"
        assert env["GITHUB_REPO"] == "PnX-SI/GeoNature"
        for bad in ["", "2.17", "v2.17.6", "2.17.6\nACTION=x", "-2.17.6"]:
            with pytest.raises(utils.UpdateRequestError):
                utils.validate_request("core", bad)

    def test_validate_module(self, app, updater, monkeypatch):
        module = {
            "code": "MYMOD",
            "directory": "/home/gn/gn_module_mymod",
            "github_repo": "org/gn_module_mymod",
            "updatable": True,
        }
        monkeypatch.setattr(utils, "get_module", lambda code: module if code == "MYMOD" else None)
        env = utils.validate_request("module", "MYMOD:1.2.3")
        assert env["MODULE_REF"] == "1.2.3"
        assert env["MODULE_DIR"] == module["directory"]
        for bad in ["OTHER:1.0.0", "MYMOD:", "MYMOD:--upload-pack=x", "MYMOD:../x", "mymod:1.0"]:
            with pytest.raises(utils.UpdateRequestError):
                utils.validate_request("module", bad)
        module["updatable"] = False
        with pytest.raises(utils.UpdateRequestError):
            utils.validate_request("module", "MYMOD:1.2.3")

    def test_write_request_rejects_injection(self, app, updater):
        with pytest.raises(utils.UpdateRequestError):
            utils.write_request({"ACTION": 'db"; rm -rf /; "'})

    def test_is_newer(self):
        assert utils.is_newer("2.17.6", "2.17.5")
        assert utils.is_newer("2.18.0", "2.17.10")
        assert not utils.is_newer("2.17.5", "2.17.5")

    def test_stale_queued_is_not_busy(self, app, updater):
        utils.write_status(state="queued", queued_ts=0)
        assert not utils.is_busy()
        utils.write_status(state="running")
        assert utils.is_busy()

    def test_task_sudo_failure(self, app, updater):
        completed = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="sudo: a password is required"
        )
        with mock.patch("geonature.core.admin.update.tasks.subprocess.run", return_value=completed):
            launch_update.run("db")
        status = utils.read_status()
        assert status["state"] == "failed"
        assert "sudo: a password is required" in status["message"]


class TestTaxrefUpdate:
    @pytest.fixture
    def next_v18(self, monkeypatch):
        monkeypatch.setattr(utils, "taxref_next_version", lambda current=None: 18)

    def _marker(self, ok, keep):
        directory = utils.state_dir() / "taxref"
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "analysis-v18.json").write_text(
            json.dumps({"ok": ok, "keep_cdnom": keep, "date": "x"})
        )

    def test_validate(self, app, updater, next_v18):
        env = utils.validate_request("taxref", "analyse:18")
        assert env["TAXREF_STEP"] == "analyse"
        assert env["TAXREF_REGION"] == "fr"
        # Saut de version, ou application sans analyse : refusé
        for bad in ["analyse:19", "apply:18", "apply:18;rm", "drop:18"]:
            with pytest.raises(utils.UpdateRequestError):
                utils.validate_request("taxref", bad)
        self._marker(ok=False, keep=False)
        with pytest.raises(utils.UpdateRequestError):
            utils.validate_request("taxref", "apply:18")
        self._marker(ok=True, keep=True)
        with pytest.raises(utils.UpdateRequestError):
            utils.validate_request("taxref", "apply:18")  # option différente de l'analyse
        assert utils.validate_request("taxref", "apply:18:keep")["TAXREF_KEEP_CDNOM"] == "1"

    def test_taxref_info(self, app):
        info = utils.taxref_info()
        assert info is not None
        assert info["taxref_count"] >= 0

    @pytest.mark.usefixtures("client_class", "temporary_transaction")
    def test_report_download(self, users, updater, next_v18):
        set_logged_user(self.client, users["admin_user"])
        report_dir = utils.taxref_work_dir(18) / "tmp"
        report_dir.mkdir(parents=True)
        (report_dir / "liste_split.csv").write_text("a,b\n")
        response = self.client.get(
            url_for("update.taxref_report", version=18, filename="liste_split.csv")
        )
        assert response.status_code == 200
        assert response.data == b"a,b\n"
        response = self.client.get(
            url_for("update.taxref_report", version=18, filename="status.json")
        )
        assert response.status_code == 404
