# Docs

| Page | What it is |
|---|---|
| [setup.md](setup.md) | Step-by-step setup of Laptop A and Laptop B, the wss link, and checks. |
| [how-it-works.md](how-it-works.md) | The whole system in plain English. Start here. |
| [dependency-graph.md](dependency-graph.md) | Which file uses which, across the client, the server and the two laptops. |
| [files/](files/README.md) | One page per file we wrote or changed: its responsibility, inputs and outputs, dependencies. |
| [phase0-findings.md](phase0-findings.md) | Hook points confirmed in the fork's source, and qwen3:14b measurements. |
| [plan.md](plan.md) | The approved phased plan (rev 3). |

The rule for every change: update the page of each file you touch, the dependency graph if an import changed, and
how-it-works.md if the behaviour changed.
