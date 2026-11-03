"""
Suivi des téléchargements de données naturalistes, par organisme producteur.

À chaque export d'observations (Synthèse, module Export), on enregistre par jour,
par export et par organisme demandeur, le nombre d'observations exportées pour
chaque jeu de données. L'organisme producteur est retrouvé au moment de la
consultation, à partir des acteurs du jeu de données (gn_meta.cor_dataset_actor).

Aucune donnée personnelle n'est enregistrée : ni id_role, ni nom, ni adresse IP.
Seul l'organisme du demandeur est conservé quand il est connu.
"""
