import pytest
import sqlalchemy as sa
from flask import current_app, url_for
from pypnusershub.tests.utils import set_logged_user

from geonature.core.export_tracking import reports, tracking
from geonature.core.gn_meta.models import CorDatasetActor
from geonature.utils.env import db


@pytest.fixture
def tracking_enabled(monkeypatch):
    cfg = dict(current_app.config["EXPORT_TRACKING"])
    cfg.update(ENABLED=True, PRODUCER_ACTOR_ROLES=["6"], SHOW_REQUESTER_ORGANISM=True)
    monkeypatch.setitem(current_app.config, "EXPORT_TRACKING", cfg)
    return cfg


@pytest.fixture
def producer_dataset(synthese_data, users):
    """Le jeu de données de la première observation a pour producteur l'organisme de admin_user."""
    obs = next(iter(synthese_data.values()))
    id_producer_role = db.session.execute(
        sa.select(sa.func.ref_nomenclatures.get_id_nomenclature("ROLE_ACTEUR", "6"))
    ).scalar()
    with db.session.begin_nested():
        db.session.add(
            CorDatasetActor(
                id_dataset=obs.id_dataset,
                id_organism=users["admin_user"].id_organisme,
                id_nomenclature_actor_role=id_producer_role,
            )
        )
    return obs.id_dataset


def _rows(**kwargs):
    start, end = reports.month_bounds()
    return reports.report_rows(start, end, **kwargs)


@pytest.mark.usefixtures("client_class", "temporary_transaction")
class TestExportTracking:
    def test_disabled_records_nothing(self, users, synthese_data):
        set_logged_user(self.client, users["self_user"])
        ids = [obs.id_synthese for obs in synthese_data.values()]
        response = self.client.post(
            url_for("gn_synthese.exports.export_observations_web"),
            json=ids,
            query_string={"export_format": "csv"},
        )
        assert response.status_code == 200
        assert (
            db.session.execute(
                sa.text("SELECT count(*) FROM gn_commons.t_export_tracking")
            ).scalar()
            == 0
        )

    def test_synthese_export_is_tracked(
        self, users, synthese_data, tracking_enabled, producer_dataset
    ):
        user = users["admin_user"]
        set_logged_user(self.client, user)
        ids = [obs.id_synthese for obs in synthese_data.values()]
        for _ in range(2):
            response = self.client.post(
                url_for("gn_synthese.exports.export_observations_web"),
                json=ids,
                query_string={"export_format": "csv"},
            )
            assert response.status_code == 200
        exported = len(response.data.decode().strip().splitlines()) - 1

        total = db.session.execute(
            sa.text("SELECT sum(nb_observations) FROM gn_commons.cor_export_tracking_dataset")
        ).scalar()
        assert total == 2 * exported
        mine = _rows(producer=user.id_organisme)
        assert mine and all(r["id_dataset"] == producer_dataset for r in mine)
        assert all(r["requester_id_organisme"] == user.id_organisme for r in mine)
        assert all(
            r["nb_requests"] == 2 for r in mine
        )  # deux exports le même jour : une ligne cumulée

        # Aucune donnée personnelle dans les tables du suivi
        columns = set(
            db.session.execute(
                sa.text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = 'gn_commons' "
                    "AND table_name IN ('t_export_tracking', 'cor_export_tracking_dataset')"
                )
            ).scalars()
        )
        assert "id_role" not in columns

    def test_my_data_page(self, users, synthese_data, tracking_enabled, producer_dataset):
        tracking.record_export(
            tracking.SOURCE_SYNTHESE_OBSERVATIONS,
            {producer_dataset: 12},
            (tracking.REQUESTER_PUBLIC, None),
            export_format="csv",
        )
        assert self.client.get(url_for("export_tracking.my_data")).status_code == 401
        set_logged_user(self.client, users["admin_user"])
        response = self.client.get(url_for("export_tracking.my_data"))
        assert response.status_code == 200
        assert "Téléchargement public (anonyme)" in response.text
        response = self.client.get(url_for("export_tracking.my_data_csv"))
        assert response.status_code == 200
        assert "12" in response.data.decode()

    def test_admin_page(self, users, tracking_enabled):
        set_logged_user(self.client, users["noright_user"])
        assert self.client.get(url_for("export_tracking_admin.index")).status_code == 403
        set_logged_user(self.client, users["admin_user"])
        assert self.client.get(url_for("export_tracking_admin.index")).status_code == 200
        assert self.client.get(url_for("export_tracking_admin.csv")).status_code == 200
        response = self.client.get(url_for("export_tracking_admin.index", producer="abc"))
        assert response.status_code == 400

    def test_record_never_raises(self, tracking_enabled, monkeypatch):
        def broken(*args, **kwargs):
            raise RuntimeError("base indisponible")

        monkeypatch.setattr(tracking, "insert", broken)
        tracking.record_export(
            tracking.SOURCE_EXPORT_API, {1: 1}, (tracking.REQUESTER_PUBLIC, None)
        )

    def test_count_rows(self, synthese_data):
        obs = list(synthese_data.values())
        counts = tracking.count_by_dataset_from_rows([{"id_synthese": o.id_synthese} for o in obs])
        assert sum(counts.values()) == len(obs)
        counts = tracking.count_by_dataset_from_rows([{"autre": 1}, {"autre": 2}])
        assert counts == {tracking.UNKNOWN_DATASET: 2}
