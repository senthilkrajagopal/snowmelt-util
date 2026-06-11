# snowmelt-util

Operational companion to [snowmelt](https://github.com/senthilkrajagopal/snowmelt)
— everything you need *around* a snowmelt deployment, none of the runtime itself.

**What lives here** (migrating from the main repo):

| Area | Contents |
|---|---|
| `charts/snowmelt-extras` | Prometheus, Grafana (+ dashboards), datagen — the observe-the-observer stack |
| `charts/snowmelt-demo` | OpenTelemetry Astronomy Shop demo wiring |
| `scripts/` | Python correctness/perf tooling (`compare_dashboards.py`, `perf_compare.py`, phase probes), deploy/redeploy scripts, disk benchmarks, k8s install helpers |
| `terraform/` | AWS dev-node, EIP, and site stacks |
| `deploy/` | Environment values files for real deployments (never commit credentials) |

**What does NOT live here:** the snowmelt runtime, its main Helm chart
(`charts/snowmelt`, versioned with the app), and `ui/dashboards`/`ui/catalog`
(compile-time embedded into the snowmelt admin binary).

## Conventions

- Branch `fable` is the active working branch.
- Scripts target the cluster/namespace conventions documented in the main
  repo's `README.md` §13.
- Credentials are never committed — values files reference Kubernetes
  Secrets or are templated from the environment.

## License

Proprietary — Copyright (c) 2026 Senthil Rajagopal, all rights reserved.
See [LICENSE](LICENSE).
