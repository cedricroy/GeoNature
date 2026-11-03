"""
Outil de mise à jour de GeoNature depuis l'interface d'administration.

Le navigateur ne lance jamais de commande lui-même : la vue d'admin envoie une
tâche Celery qui valide la demande, l'écrit dans ``request.env`` puis démarre le
service systemd ``geonature-update.service``. C'est ce service (et non le worker
Celery, qui est arrêté pendant une mise à jour) qui exécute
``install/update/geonature-update.sh``.
"""
