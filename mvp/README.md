# MVP Python - Agent de supervision

## Ce que fait le MVP

- lance un process local de miner si configure
- surveille sa disponibilite
- interroge une source de telemetrie:
  - API XMRig
  - endpoint HTTP JSON generique pour ASIC
- applique un watchdog simple
- redemarre le miner local si necessaire
- ecrit des logs JSON dans stdout

## Fichiers

- [agent.py](/Users/a.leroux/Documents/Perso/Workspace/trading_crypto/mvp/agent.py)
- [config.example.json](/Users/a.leroux/Documents/Perso/Workspace/trading_crypto/mvp/config.example.json)

## Lancer

```bash
cd /Users/a.leroux/Documents/Perso/Workspace/trading_crypto/mvp
python3 agent.py --config config.example.json
```

## Notes

- Le mode `xmrig` attend une API HTTP active cote XMRig.
- Le mode `generic_http` attend un JSON avec quelques champs standards.
- Le MVP privilegie la simplicite et la lisibilite.

## Format attendu pour `generic_http`

Le JSON peut ressembler a ceci:

```json
{
  "hashrate": 12345,
  "accepted": 100,
  "rejected": 2,
  "temperature_c": 67,
  "power_w": 1100
}
```
