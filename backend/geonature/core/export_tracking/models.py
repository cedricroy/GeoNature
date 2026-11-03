from geonature.utils.env import db


class TExportTracking(db.Model):
    """Téléchargements agrégés par jour, export, format et organisme demandeur."""

    __tablename__ = "t_export_tracking"
    __table_args__ = {"schema": "gn_commons"}

    id_tracking = db.Column(db.Integer, primary_key=True)
    # Clé de regroupement (jour|source|export|format|type demandeur|organisme), unique
    tracking_key = db.Column(db.String(255), nullable=False, unique=True)
    day = db.Column(db.Date, nullable=False, index=True)
    source = db.Column(db.String(50), nullable=False)
    id_export = db.Column(db.Integer)
    export_label = db.Column(db.String(255))
    export_format = db.Column(db.String(20))
    requester_type = db.Column(db.String(20), nullable=False)
    requester_id_organisme = db.Column(
        db.Integer, db.ForeignKey("utilisateurs.bib_organismes.id_organisme", ondelete="SET NULL")
    )
    nb_requests = db.Column(db.Integer, nullable=False, default=0)

    datasets = db.relationship("CorExportTrackingDataset", lazy="select", viewonly=True)


class CorExportTrackingDataset(db.Model):
    """Nombre d'observations exportées par jeu de données (0 = jeu de données inconnu)."""

    __tablename__ = "cor_export_tracking_dataset"
    __table_args__ = {"schema": "gn_commons"}

    id_tracking = db.Column(
        db.Integer,
        db.ForeignKey("gn_commons.t_export_tracking.id_tracking", ondelete="CASCADE"),
        primary_key=True,
    )
    # Pas de clé étrangère : l'historique est conservé si le jeu de données est supprimé.
    id_dataset = db.Column(db.Integer, primary_key=True)
    nb_observations = db.Column(db.BigInteger, nullable=False, default=0)
