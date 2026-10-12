# EU Careers Tracker V2

Application web personnelle et indépendante d'EPSO. Les offres sont collectées depuis les tableaux EU Careers. Les données ne sont pas garanties exhaustives.

## Remplacer la V1.1 sur GitHub / Render

1. **Sauvegardez les données** : téléchargez le CSV depuis l'ancienne application. Ce CSV n'est pas une sauvegarde complète des favoris/historiques ; pour les préserver, sauvegardez aussi le fichier SQLite depuis le disque existant si vous y avez accès.
2. À la racine du dépôt GitHub, remplacez `app.py`, `requirements.txt` et `render.yaml` ; **ajoutez** `index.html`. Si l'ancienne application était dans un sous-dossier, gardez la même structure que le `Root Directory` configuré dans Render.
3. Commit/push. Render redéploie normalement automatiquement. Sinon cliquez sur **Manual Deploy → Deploy latest commit**.
4. Dans Render, **Environment**, ajoutez `TRACKER_PASSWORD` (mot de passe fort). Le navigateur demandera des identifiants Basic Auth : nom d'utilisateur quelconque, mot de passe = variable. Utilisez impérativement HTTPS.
5. Dans l'application, cliquez sur **Actualiser**. Ouvrez une fiche et cliquez sur **Extraire page et PDF** si elle n'est pas enrichie.

## Persistance : important

Render Free utilise un système de fichiers éphémère : **la base SQLite et les historiques peuvent être perdus au redéploiement, redémarrage ou mise en veille**. Un CSV ne permet pas de restaurer les historiques. Pour une vraie persistance, utilisez un service Render avec disque persistant et `DB_PATH=/var/data/tracker.sqlite3`, ou adaptez l'application à PostgreSQL. Les disques persistants Render nécessitent une offre compatible, et ne doivent pas être déclarés sur le plan Free. La V2 ne configure donc pas de disque par défaut. Si vous utilisez un disque, il faut aussi prévoir des sauvegardes externes.

## Alertes email (optionnel)

Configurez les variables d'environnement suivantes dans Render :

- `SMTP_HOST` : hôte SMTP de votre fournisseur
- `SMTP_PORT` : `587` (STARTTLS) ou `465` (TLS)
- `SMTP_USER` et `SMTP_PASSWORD` : identifiants SMTP si requis
- `ALERT_FROM` : adresse expéditrice autorisée
- `ALERT_TO` : adresse destinataire

Les nouvelles annonces **FG IV** sont notifiées après synchronisation ; les échéances sont vérifiées à J-7, J-3 et J-1. Les emails sont envoyés uniquement si SMTP est configuré et fonctionnel. Sans base persistante, des alertes peuvent être envoyées plusieurs fois après perte de données. Les alertes dépendent du fonctionnement du service : Render Free peut s'endormir, et **la boucle de synchronisation n'est pas un ordonnanceur fiable**. Pour une garantie de passage quotidien, utilisez un service toujours actif ou un cron externe authentifié. Les dates sans format reconnu ne déclenchent pas d'alerte ; l'heure exacte doit être vérifiée sur l'annonce officielle.

## Limites de la V2

- Seules les pages officielles `eu-careers.europa.eu` et `selection.eu-careers.europa.eu` sont acceptées pour la collecte et l'extraction PDF. Les PDF hébergés sur d'autres domaines officiels ne sont pas téléchargés automatiquement.
- L'extraction PDF utilise `pypdf` et ne fait **pas** d'OCR ; certains PDF scannés resteront vides.
- La recherche intégrale fonctionne sur le texte **déjà extrait**. La synchronisation enrichit au maximum `MAX_PDFS_PER_SYNC` nouvelles fiches par cycle (défaut 15), et l'utilisateur peut enrichir une fiche manuellement.
- Les offres disparues sont archivées uniquement après une collecte de source complète, selon les règles de pagination du site. La disparition ne prouve pas que le recrutement est clos.
- Les emails et favoris sont communs à tous les utilisateurs autorisés. Le site n'a pas de comptes personnels séparés.
- L'application n'est pas une source officielle de vérité. Vérifiez chaque annonce avant de postuler.

## Test local

```bash
python -m pip install -r requirements.txt
AUTO_SYNC_HOURS=0 python app.py
```

Sous Windows PowerShell :

```powershell
py -m pip install -r requirements.txt
$env:AUTO_SYNC_HOURS="0"
py app.py
```

Ouvrez `http://127.0.0.1:8765`. Pour des tests sans accès Internet, importez `app` et testez `parse()` avec une table HTML factice.
