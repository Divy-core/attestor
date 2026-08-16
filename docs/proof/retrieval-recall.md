# Retrieval recall — raw question text vs expanded queries

`make recall` · 63 hand-labelled pairs · recall@5 · expansion: heuristic only
· elapsed 120.5s

## Result

| | recall@5 |
|---|---|
| Raw question text (baseline) | **90%** |
| Expanded queries | **95%** |
| Delta | **+5%** |

## By department

| department | pairs | raw | expanded |
|---|---|---|---|
| engineering | 20 | 90% | 95% |
| legal | 17 | 94% | 94% |
| security | 26 | 88% | 96% |

## Cases expansion fixed (3)

- `CMEK` → `encryption-standard` (security)
- `MFA` → `access-control-standard` (security)
- `What is your Recovery Time Objective?` → `backup-restore-procedure` (engineering)

## Still missing (3)

- `How long do elevated production access grants remain valid?` wanted `access-control-standard`, got `nothing`
- `Which transfer mechanism do you rely on for transfers out of the EEA?` wanted `standard-contractual-clauses-summary`, got `nothing`
- `RTO RPO` wanted `backup-restore-procedure`, got `nothing`
