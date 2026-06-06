# Comment marche l'algorithme du projet

## But du projet

Le projet n'implemente pas l'algorithme cryptographique de minage de Monero lui-meme.
Le hash RandomX est execute par **XMRig**.

Le role du code Python est different:

- lancer XMRig
- surveiller son etat
- lire sa telemetrie
- decider quand redemarrer ou declarer un incident
- exposer un etat local via une API
- produire des recommandations d'optimisation

Autrement dit, l'"algorithme" du projet est surtout un **algorithme de supervision et de decision**.

## Vue d'ensemble

Le systeme est organise autour de 6 blocs:

- `config.py`: charge et valide la configuration
- `process_manager.py`: gere le process XMRig
- `telemetry.py`: interroge l'API HTTP de XMRig
- `state.py` + `models.py`: maintiennent l'etat courant du rig
- `watchdog.py`: decide si le rig est sain, degrade ou a redemarrer
- `service.py`: orchestre la boucle principale

Les endpoints HTTP dans `api.py` exposent ensuite cet etat au monde exterieur.

## Idee centrale

Le systeme fonctionne comme une boucle repetee:

1. verifier si le process XMRig tourne
2. lire la telemetrie HTTP de XMRig
3. mettre a jour l'etat interne du rig
4. faire passer cet etat dans le watchdog
5. produire une decision
6. executer l'action si necessaire

La logique de supervision est donc un **cycle perception -> evaluation -> action**.

## Entrees de l'algorithme

L'algorithme s'appuie sur 4 grandes categories d'entrees.

### 1. Configuration statique

Elle vient de `AppConfig` dans [models.py](/Users/a.leroux/Documents/Perso/Workspace/trading_crypto/src/miner_supervisor/models.py) et du fichier YAML.

Parmi les champs les plus importants:

- `poll_interval_seconds`
- `warmup_seconds`
- `restart_if_below_for_seconds`
- `restart_backoff_seconds`
- `max_restart_attempts`
- `thresholds.min_hashrate`
- `thresholds.max_rejection_ratio`
- `thresholds.max_telemetry_failures`
- `thresholds.telemetry_restart_grace_seconds`

Ces valeurs definissent les seuils de decision.

### 2. Etat du process

Le process manager indique:

- si XMRig tourne
- son PID
- si un demarrage ou un redemarrage a echoue

### 3. Telemetrie XMRig

La telemetrie est lue via l'API HTTP de XMRig, puis transformee en `TelemetrySnapshot`.

Le snapshot contient notamment:

- `hashrate`
- `hashrate_1m`
- `hashrate_10m`
- `hashrate_15m`
- `accepted`
- `rejected`
- `stale`
- `uptime_seconds`
- `active_pool`
- `worker_name`
- `api_latency_ms`

### 4. Etat interne memorise

Le service garde un `RigState`, c'est-a-dire une memoire du rig.

Cet etat contient:

- l'etat logique courant
- la sante globale
- le dernier hashrate connu
- le dernier moment ou le hashrate etait juge "bon"
- le nombre d'echecs consecutifs de telemetrie
- le nombre de restart deja tentes
- la raison du dernier restart
- l'indicateur `needs_manual_intervention`

Cette memoire est essentielle, car l'algorithme ne decide pas uniquement avec la valeur instantanee. Il prend aussi en compte l'historique recent.

## Machine a etats

Le cycle de vie du rig repose sur `RigLifecycleState`.

Les etats possibles sont:

- `starting`
- `warming_up`
- `healthy`
- `degraded`
- `restarting`
- `stopped`
- `failed`
- `needs_manual_intervention`

### Sens de chaque etat

`starting`

- etat initial logique
- le service demarre ou re-demarre sa supervision

`warming_up`

- XMRig vient d'etre lance ou relance
- pendant cette fenetre, le hashrate bas ne provoque pas encore un restart
- cela evite les faux positifs pendant l'initialisation

`healthy`

- la telemetrie est valide
- le hashrate atteint ou depasse le seuil configure
- le rig est considere comme sain

`degraded`

- le rig fonctionne mais quelque chose n'est pas ideal
- par exemple hashrate insuffisant, telemetrie temporairement indisponible, ou ratio de rejet trop haut

`restarting`

- le watchdog a decide qu'un restart etait necessaire

`stopped`

- le miner est arrete
- soit parce qu'il est desactive, soit parce qu'une action d'arret a ete demandee

`failed`

- un demarrage ou redemarrage a echoue
- par exemple binaire absent, droits insuffisants ou lancement impossible

`needs_manual_intervention`

- le systeme a atteint un etat de panne durable
- le budget de restart est epuise
- l'algorithme cesse de boucler automatiquement

## Flux detaille d'une iteration

Le coeur de l'algorithme est dans [service.py](/Users/a.leroux/Documents/Perso/Workspace/trading_crypto/src/miner_supervisor/service.py), surtout `run_iteration()`.

### Etape 1. Lire l'etat du process

Le service commence par demander au `ProcessManager`:

- `is_running()`
- `pid()`

Puis il recopie ces informations dans le `StateStore`.

But:

- avoir un etat coherent avant d'evaluer la telemetrie
- detecter un process mort meme si l'API HTTP n'a pas encore ete interrogee

### Etape 2. Recuperer la telemetrie

Le `XmrigTelemetryClient`:

- reutilise une connexion HTTP persistante si possible
- appelle l'endpoint XMRig configure
- lit le JSON de reponse
- transforme ce JSON en `TelemetrySnapshot`

La fonction de parsing:

- extrait le hashrate
- calcule les shares acceptees et rejetees
- determine le pool actif
- mesure la latence de l'appel

### Etape 3. Mettre a jour l'etat interne

Quand la telemetrie est bonne, `apply_telemetry()` met a jour:

- `last_hashrate`
- `accepted`
- `rejected`
- `stale`
- `rejection_ratio`
- `uptime_seconds`
- `active_pool`
- `worker_name`
- `api_latency_ms`
- `last_telemetry_success_at`
- `last_telemetry_error`
- `consecutive_telemetry_failures`

Le point cle de l'algorithme est ici:

- `last_good_hashrate_at` n'est mis a jour **que si** `snapshot.hashrate >= min_hashrate`

Cela veut dire qu'un hashrate faible mais non nul n'est pas considere comme "bon". C'est fondamental pour detecter un rig qui tourne mais mine mal.

### Etape 4. Gerer les erreurs de telemetrie

Si la telemetrie echoue:

- `last_telemetry_error` est renseigne
- `consecutive_telemetry_failures` est incremente

L'algorithme ne restart pas forcement tout de suite.
Il laisse au watchdog le soin de decider si l'echec est transitoire ou durable.

### Etape 5. Evaluer le watchdog

Le watchdog est un bloc de decision pur dans [watchdog.py](/Users/a.leroux/Documents/Perso/Workspace/trading_crypto/src/miner_supervisor/watchdog.py).

Il prend 4 entrees:

- la configuration
- le `RigState`
- le `TelemetrySnapshot` si disponible
- l'erreur de telemetrie si disponible

Et il renvoie un `WatchdogDecision`:

- `action`
- `reason`
- `severity`
- `cooldown_seconds`
- `target_state`

### Etape 6. Mettre a jour l'etat logique

Une fois la decision produite:

- le `target_state` devient le nouvel etat
- `health` est derive de cet etat
- `last_decision` est memorise
- si l'etat final est `healthy`, `restart_attempts` est remis a zero

Cette remise a zero signifie qu'une reprise saine efface la dette de restart accumulee.

### Etape 7. Executer l'action

Si la decision contient une action:

- `START`
- `RESTART`
- `STOP`
- `MARK_FAILED`

alors le service appelle la methode correspondante.

## Logique du watchdog

La force du projet est ici: le watchdog est simple, deterministe et testable.

### Regle 1. Intervention manuelle prioritaire

Si `needs_manual_intervention == True`:

- aucune relance automatique
- le systeme reste en `needs_manual_intervention`

### Regle 2. Process absent

Si le miner n'est pas en cours d'execution:

- si le budget de restart est epuise -> `MARK_FAILED`
- sinon -> `START`

### Regle 3. Warmup

Si `now < warmup_until`:

- pas de sanction sur hashrate faible
- etat cible `warming_up`

Cette phase protege contre les redemarrages trop agressifs au boot.

### Regle 4. Telemetrie indisponible

Si la telemetrie echoue:

- si les echecs sont encore dans une zone acceptable -> `degraded`
- si le nombre d'echecs consecutifs est trop haut et que la grace period est depassee -> `RESTART`
- si le budget de restart est deja epuise -> `MARK_FAILED`

### Regle 5. Ratio de rejet trop eleve

Si `rejection_ratio > max_rejection_ratio`:

- etat cible `degraded`
- pas de restart immediat

L'idee ici est qu'un mauvais pool ou une mauvaise latence n'exigent pas toujours un restart local instantane.

### Regle 6. Hashrate sain

Si `hashrate >= min_hashrate`:

- etat cible `healthy`
- pas d'action

### Regle 7. Hashrate bas durable

Si `hashrate < min_hashrate`:

- on regarde `last_good_hashrate_at`
- si le temps ecoule est inferieur a `restart_if_below_for_seconds` -> `degraded`
- sinon -> `RESTART`
- si le budget est deja epuise -> `MARK_FAILED`

Cette regle est le coeur de la logique "un rig tourne, mais pas assez bien".

## Algorithme de start / stop / restart

### Start

Quand `start_miner()` est appelee:

1. si `miner.enabled == false`, le service passe en `stopped`
2. sinon il tente `process_manager.start()`
3. si le lancement echoue:
   - etat `failed`
   - journalisation de l'erreur
4. si le lancement reussit:
   - etat `warming_up`
   - `started_at` mis a jour
   - `warmup_until` calcule

### Stop

Quand `stop_miner()` est appelee:

1. le process est termine proprement
2. la connexion telemetry persistante est fermee
3. l'etat passe a `stopped`

### Restart

Quand `restart_miner()` est appelee:

1. `restart_attempts` est incremente
2. le process est stoppe
3. attente du backoff configure
4. tentative de relance
5. si succes:
   - etat `warming_up`
6. si echec:
   - etat `failed`

## Role du StateStore

Le `StateStore` est une petite memoire synchronisee:

- un lock protege l'etat
- `snapshot()` renvoie une copie du state
- `update()` modifie uniquement les champs necessaires
- `bump()` incremente un compteur
- un buffer garde les evenements recents

Ce design sert 2 objectifs:

- eviter les reconstructions couteuses d'objets a chaque update
- permettre un acces coherent depuis la boucle de supervision et l'API

## Telemetrie XMRig

Le client de telemetrie fait 2 choses:

### 1. Communication

- il parse l'URL
- il ouvre une connexion `HTTPConnection` ou `HTTPSConnection`
- il la reutilise tant que l'autorite ne change pas
- il ferme la connexion sur erreur

### 2. Normalisation

L'API XMRig brute est transformee en modele stable.

Exemples:

- `hashrate.total[0]` devient `hashrate`
- `shares_good` devient `accepted`
- `shares_total - shares_good` devient `rejected` si `shares_bad` n'est pas expose
- le pool actif est derive de la liste des pools ou de `connection.pool`

Le but est d'isoler tout le reste du code des details du JSON XMRig.

## Calcul de la sante globale

La sante `health` est une projection simplifiee de l'etat logique:

- `healthy` ou `warming_up` -> `ok`
- `degraded`, `restarting`, `starting` -> `degraded`
- `failed`, `stopped`, `needs_manual_intervention` -> `failed`

Cette simplification sert surtout a l'API `/health`.

## Optimisation XMRig et Linux

Le module [optimization.py](/Users/a.leroux/Documents/Perso/Workspace/trading_crypto/src/miner_supervisor/optimization.py) ajoute un second niveau d'algorithme: un **algorithme de diagnostic d'optimisation**.

Il ne change pas le hash lui-meme. Il observe et recommande.

### Profils

Le projet supporte trois profils:

- `balanced`
- `efficient`
- `max_hashrate`

Chaque profil influence surtout:

- `cpu_max_threads_hint`
- `cpu_yield`
- le niveau d'agressivite attendu

### Generation de config XMRig

`build_xmrig_config()` produit une configuration XMRig a partir de:

- la config applicative
- les pools definis
- le profil d'optimisation
- les options RandomX

Cette generation sert a:

- standardiser les options
- produire un rendu explicite du profil
- preparer une future ecriture automatique de `xmrig.json`

### Detection d'optimisations host

`detect_host_optimizations()` lit ce qu'il peut sur la machine:

- nombre de CPU
- huge pages Linux
- taille des huge pages
- presence de 1GB pages
- Transparent Huge Pages
- nombre de noeuds NUMA

Puis il calcule des recommandations, par exemple:

- activer les huge pages
- tester les 1GB pages
- verifier le mode NUMA
- ajuster `max-threads-hint`

### Endpoint de synthese

`/optimization-status` renvoie:

- le profil actif
- la config XMRig generee
- les observations host
- le runtime courant
- les recommandations

## API HTTP exposee

L'API est locale et simple.

### Lecture

- `GET /health`
- `GET /status`
- `GET /metrics`
- `GET /events`
- `GET /optimization-status`

### Controle

- `POST /control/start`
- `POST /control/stop`
- `POST /control/restart`

Les endpoints de controle exigent `X-API-Token`.

## Strategie de logs et evenements

Le projet separe maintenant deux niveaux:

- les evenements importants sont gardes dans le buffer recent
- les evenements de routine saine peuvent etre logges en `DEBUG` sans etre stockes

Exemple:

- une telemetrie saine reguliere ne pollue plus inutilement le buffer
- une erreur de telemetrie ou un restart restent visibles

## Pourquoi l'algorithme est robuste

Il est robuste pour plusieurs raisons:

- il se base a la fois sur l'instantane et sur l'historique
- il evite les decisions brutales pendant le warmup
- il limite les boucles infinies grace au budget de restart
- il passe en intervention manuelle quand l'automatisation n'est plus saine
- il separe clairement la logique de decision du code I/O

## Ce que l'algorithme ne fait pas

Il est important de bien cadrer le perimetre:

- il ne calcule pas RandomX lui-meme
- il ne remplace pas XMRig
- il n'auto-tune pas encore reellement la machine via benchmark
- il n'ajuste pas encore dynamiquement les threads ou l'affinite CPU
- il ne pilote pas encore la temperature ou la puissance de facon active
- il n'orchestratione pas encore une flotte multi-rigs

## Resume tres concret

Si on resume l'algorithme en une phrase:

> Le projet observe XMRig, memorise l'historique utile, applique des regles de sante, puis agit automatiquement tant que la situation reste recuperable.

Et si on le resume en pseudo-flux:

1. demarrer XMRig
2. attendre le warmup
3. interroger la telemetrie
4. mettre a jour l'etat
5. verifier hashrate, shares, telemetrie et budget de restart
6. decider: sain, degrade, restart ou echec manuel
7. exposer le resultat via API et recommandations d'optimisation

## Fichiers a lire pour comprendre l'algo

Si tu veux suivre la logique directement dans le code, lis dans cet ordre:

1. [models.py](/Users/a.leroux/Documents/Perso/Workspace/trading_crypto/src/miner_supervisor/models.py)
2. [state.py](/Users/a.leroux/Documents/Perso/Workspace/trading_crypto/src/miner_supervisor/state.py)
3. [telemetry.py](/Users/a.leroux/Documents/Perso/Workspace/trading_crypto/src/miner_supervisor/telemetry.py)
4. [watchdog.py](/Users/a.leroux/Documents/Perso/Workspace/trading_crypto/src/miner_supervisor/watchdog.py)
5. [service.py](/Users/a.leroux/Documents/Perso/Workspace/trading_crypto/src/miner_supervisor/service.py)
6. [optimization.py](/Users/a.leroux/Documents/Perso/Workspace/trading_crypto/src/miner_supervisor/optimization.py)
7. [api.py](/Users/a.leroux/Documents/Perso/Workspace/trading_crypto/src/miner_supervisor/api.py)
