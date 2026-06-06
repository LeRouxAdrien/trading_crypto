# Miner Supervisor

Superviseur local Linux-first pour **Monero (XMR)** autour de **XMRig**.

Le projet est concu pour un usage **legitime** sur une machine que tu controles:

- lancement et supervision de XMRig
- watchdog intelligent
- telemetrie XMRig normalisee
- API locale de statut et de controle
- logs JSON
- tests unitaires
- deploiement via `systemd`

Le projet ne contient **aucune** capacite de furtivite, propagation, persistence malveillante, controle distant anonyme ou minage non autorise.

## Arborescence

- [config/config.example.yaml](/Users/a.leroux/Documents/Perso/Workspace/trading_crypto/config/config.example.yaml)
- [src/miner_supervisor/main.py](/Users/a.leroux/Documents/Perso/Workspace/trading_crypto/src/miner_supervisor/main.py)
- [src/miner_supervisor/service.py](/Users/a.leroux/Documents/Perso/Workspace/trading_crypto/src/miner_supervisor/service.py)
- [src/miner_supervisor/watchdog.py](/Users/a.leroux/Documents/Perso/Workspace/trading_crypto/src/miner_supervisor/watchdog.py)
- [deploy/miner-supervisor.service](/Users/a.leroux/Documents/Perso/Workspace/trading_crypto/deploy/miner-supervisor.service)
- [tests](/Users/a.leroux/Documents/Perso/Workspace/trading_crypto/tests)

## Architecture resumee

Le produit se compose de 6 briques:

- `config`: chargement JSON/YAML et validation Pydantic
- `process_manager`: demarrage, arret et restart de XMRig
- `telemetry`: lecture de l'API XMRig et normalisation des donnees
- `watchdog`: logique pure et testable
- `state`: etat courant du rig et historique court
- `api`: endpoints locaux `health`, `status`, `metrics` et controle authentifie

## Installation

```bash
cd /Users/a.leroux/Documents/Perso/Workspace/trading_crypto
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Configuration

Copie puis adapte:

```bash
cp /Users/a.leroux/Documents/Perso/Workspace/trading_crypto/config/config.example.yaml ./config.local.yaml
```

Points importants:

- `security.control_token`: token requis pour les endpoints de controle
- `miner.command`: commande exacte pour lancer XMRig
- `telemetry.url`: endpoint HTTP XMRig, souvent `http://127.0.0.1:18080/2/summary`
- `pools`: inventaire logique des pools pour diagnostic et evolution future

## Lancement manuel

```bash
source .venv/bin/activate
python -m miner_supervisor.main --config /Users/a.leroux/Documents/Perso/Workspace/trading_crypto/config/config.example.yaml
```

## API locale

Par defaut, l'API se bind sur `127.0.0.1`.

- `GET /health`
- `GET /status`
- `GET /metrics`
- `GET /events`
- `GET /optimization-status`
- `POST /control/start`
- `POST /control/stop`
- `POST /control/restart`

Les endpoints `POST /control/*` exigent l'en-tete `X-API-Token`.

Exemple:

```bash
curl -H "X-API-Token: change-me" -X POST http://127.0.0.1:8080/control/restart
```

Notes:

- `GET /metrics` reste compact pour les scrapes frequents
- `GET /events` expose l'historique recent d'evenements
- `GET /optimization-status` expose les checks Linux/XMRig et une config generee pour le profil actif

## Strategie watchdog

Le watchdog applique ces regles:

- process absent => tentative de start ou restart
- warmup apres chaque demarrage
- hashrate durablement sous seuil => restart
- trop d'echecs de telemetrie => restart
- ratio de shares rejetees trop haut => etat degrade
- budget de redemarrages depasse => `needs_manual_intervention`

## Tests

```bash
source .venv/bin/activate
pytest
```

## systemd

Exemple fourni dans [deploy/miner-supervisor.service](/Users/a.leroux/Documents/Perso/Workspace/trading_crypto/deploy/miner-supervisor.service).

## Limites connues

- le failover effectif de pool reste delegue a XMRig ou a une reconfiguration externe
- la temperature et la puissance ne sont pas toujours exposees par XMRig
- le projet vise une machine locale, pas encore un control plane multi-rigs

## Roadmap

- export Prometheus
- persistance SQLite des evenements
- support multi-rigs
- support d'autres miners via adaptateurs
