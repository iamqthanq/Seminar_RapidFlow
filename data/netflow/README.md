# NetFlow dataset — checked provenance and compatibility

## Local distribution (2026-11-04)

- Files downloaded from published NewSP (ICDE 2024) author repository: https://github.com/hnuGraph/NewSP
- Author repository SHA at acquisition: `c5c6eb05ce5059be0fa803d7a29d57cc26060a25`.
- Official `netflow.zip`: https://raw.githubusercontent.com/hnuGraph/NewSP/main/netflow.zip
- GitHub blob SHA: `414d90ec03795bfaa1a7e109f398a8412700549a`.
- ZIP SHA-256: `19460c5a69723ba108c484de26da79a616edff915f76de3f6bca7f6a12f4e66a`.
- Archive is at `source_archive/netflow.zip`; extracted without modification to `release_newsp/netflow/`. Both are ignored by outer Git.
- SHA-256 `Ndata.graph`: `bfedd2cab6d2c460141da945e77b0be39ae24c74d9a28b9394a38ee35b652fc6`.
- SHA-256 `Ninsertion.graph`: `e45f2d908eaed5098653d4635076bef0152438014ad6e3aa03df522cef85ab05`.

## What was verified

The file `release_newsp/netflow/data_graph/Ndata.graph` is **RapidFlow-compatible in record syntax**, containing `v ID label` and `e source target label`. File audit found:
- 3,114,895 vertices and 2,849,732 edges;
- one vertex label (0) and eight distinct edge labels (0 through 7);
- observed maximum undirected degree 49,014;
- no malformed records in `Ndata.graph`.

`Ninsertion.graph` contains 10,000 insertions. Five `Ndeletion10/20/30/40/50.graph` files contain 10,000 mixed insert/delete operations each. A read-only audit on the initial graph found **zero invalid update operations** across those six streams (edge-exists / edge-missing checks).

`SData.graph` has a *different* format (`t n m`, `v id label degree`, etc.) and is **NOT** a drop-in replacement for the RapidFlow input `Ndata.graph`.

The `Nquery/` directory contains 2,700 files; **30** under `staticCaseStudyQuery/` use a different format and must not be passed as-is. Also `Nquery/Nquery180_8/Q_136` contains edge label **32513**, not present in `Ndata.graph`; mark this query as inconsistent, and do not benchmark it by default. Do not silently relabel or delete source files.

## Critical comparability limitation

This is the **NewSP NetFlow distribution**, not a verified byte-identical copy of RapidFlow's PVLDB 2022 dataset.
- RapidFlow Table 3 reports 7 edge labels; this release has 8.
- The max degree and update workloads may also differ.
- NewSP uses 10,000-edge streams, whereas RapidFlow describes a 10% edge insertion/deletion rate in its experimental protocol.
- RapidFlow README points to https://github.com/RapidsAtHKUST/ContinuousSubgraphMatching where the dataset link is hosted on HKUST SharePoint. That link returned HTTP 404 from the Linux device on 2026-11-04.

**Permitted interpretation:** reproducible RapidFlow experiment on a clearly versioned public NetFlow CSM dataset, provided implementation compatibility and correctness are confirmed first. **Not yet permitted:** claim exact replication of the original RapidFlow paper's benchmark.

Source archives, extracted graphs, and logs are kept outside Git. See `configs/netflow_newsp_manifest.json` for the machine-readable record.
