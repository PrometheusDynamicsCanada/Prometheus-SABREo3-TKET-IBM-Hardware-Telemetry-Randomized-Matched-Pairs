# Prometheus: Randomized Instance Robustness Benchmark

## Executive Summary
This repository tests a critical follow-up question from the Prometheus hardware-routing experiments:

> **Does the observed routing-cost/fidelity tradeoff survive when the logical problem instances themselves are randomized?**

A result obtained from a small number of carefully selected circuits can always raise the possibility of selection bias. To address that concern, this benchmark evaluates randomized logical instances spanning QAOA and CrossEnt workloads at $N=6$ through $N=9$. For each instance, the same logical problem is compiled independently by SABRE O3, TKET, and Prometheus and executed on the same IBM Heron processor.

Across the matched hardware comparisons, Prometheus achieved higher measured Hellinger fidelity than SABRE O3 in 30 of 40 randomized instances (75%), despite generally accepting substantially higher routing cost.

The result does not establish universal superiority. Instead, it strengthens the narrower observation that the routing-cost/fidelity separation is not confined to a small collection of manually selected circuits.

The repository contains the per-instance telemetry, compiled circuits, hardware execution artifacts, raw counts, and verification tooling required to independently reproduce the reported distribution metrics.

### Results at a Glance
| Benchmark | Instances | Prometheus vs SABRE | Routing tradeoff |
| :--- | :--- | :--- | :--- |
| **QAOA + CrossEnt** | 40 | 30/40 (75%) | Prometheus generally uses higher routing cost |
| **CrossEnt N=9** | 5 | 4/5 (80%) | Up to +971 additional 2Q gates per instance |
| **QAOA N=6–9** | 20 | Instance-dependent | Higher cost can coincide with higher fidelity |

> **Important:** The 75% figure is a matched-instance comparison against SABRE O3. It is not a claim that Prometheus wins 75% of all possible quantum circuits, nor that the result is statistically sufficient by itself to establish universal superiority.

---

## 1. Why Randomization Matters
The initial Prometheus experiments identified workloads where accepting additional routing cost was associated with higher measured Hellinger fidelity. That raises an important question:

*Could the effect simply result from selecting logical circuits that happen to favor Prometheus?*

This benchmark addresses that concern by varying the underlying logical problem instances rather than evaluating only a small number of manually selected circuits. 

For each randomized instance:
* A logical problem instance is generated.
* The same logical circuit is provided independently to each compiler.
* SABRE O3, TKET, and Prometheus produce separate physical implementations.
* The resulting circuits are executed on the same hardware.
* The measured distributions are mapped back into the logical basis.
* Hellinger fidelity is calculated against the exact ideal logical distribution.
* The compiler outputs are compared on a matched-instance basis.

The important comparison is therefore not between unrelated circuits, but between different physical realizations of the same randomized logical instance.

---

## 2. Experimental Question
The benchmark tests whether the following regime occurs repeatedly:

$$\Delta C_{\mathrm{routing}} > 0 \quad \text{while simultaneously observing} \quad \Delta F_H > 0$$

where:
* $\Delta C_{\mathrm{routing}}$ is the additional physical routing cost incurred by Prometheus relative to SABRE O3.
* $\Delta F_H$ is the change in measured Hellinger fidelity.
* $F_H$ measures agreement between the experimentally observed logical distribution and the exact ideal distribution.

The interesting region is therefore:
**Higher routing cost + higher measured fidelity**

This does not imply that the additional SWAPs or 2Q gates are individually beneficial. It means that the complete physical implementation, including its qubit placement, coupler selection, routing sequence, and additional operations, can produce a better measured logical distribution than a lower-cost implementation on the tested hardware.

---

## 3. Benchmark Composition
The randomized benchmark spans two workload families.

### Randomized QAOA
QAOA instances use randomized problem structures, including randomized graph topologies. The benchmark covers:
* $N=6$
* $N=7$
* $N=8$
* $N=9$

Multiple independently generated instances are evaluated at each problem size.

### Randomized CrossEnt
CrossEnt provides denser interaction structures and therefore places substantially greater routing pressure on the compiler. The benchmark similarly covers:
* $N=6$
* $N=7$
* $N=8$
* $N=9$

This workload is particularly useful for identifying the point at which the additional routing cost ceases to be compensated by the physical-placement effect.

---

## 4. Hardware Execution
* **Target hardware:** IBM Heron architecture — `ibm_marrakesh`
* **Execution methodology:** Matched compiler outputs executed through Qiskit Runtime SamplerV2.
* **Logical instances:** 40
* **Compiler pipelines:** SABRE O3, TKET, Prometheus
* **Compiler outputs:** 120
* **Hardware shots:** 491,520 total
* **Execution structure:** Interleaved compiler outputs within a unified SamplerV2 job (`job-da1do46g52gs73clh7c0`).

The interleaved execution structure is intended to reduce confounding from temporal hardware drift by preventing one compiler from being evaluated exclusively before or after another compiler. Dynamical decoupling and Pauli twirling were disabled for the experiment.

---

## 5. Fidelity Metric
The primary distributional metric is Hellinger fidelity. For experimentally observed distribution $P$ and exact ideal logical distribution $Q$:

$$F_H(P, Q) = \left( \sum_x \sqrt{P(x)Q(x)} \right)^2$$

where:
* $P(x)$ is the measured probability of logical outcome $x$.
* $Q(x)$ is the exact ideal probability of the same logical outcome.

Before calculating fidelity, the hardware measurement results are mapped back through the compiler's physical-to-logical mapping. A higher value indicates closer agreement with the ideal logical distribution.

The repository also reports Total Variation Distance (TVD):

$$D_{\mathrm{TV}}(P, Q) = \frac{1}{2} \sum_x \vert{}P(x) - Q(x)\vert{}$$

These metrics are retained separately so that the conclusion does not depend on a single scalar measure.

---

## 6. Strongest Randomized Evidence: CrossEnt N=9
The densest CrossEnt workload produces one of the clearest demonstrations of the routing-cost/fidelity separation. At $N=9$, Prometheus uses a substantially larger physical circuit than SABRE O3.

For example, the randomized telemetry includes Prometheus implementations with:
* 1,164 routed abstract 2Q operations
* 72 logical abstract 2Q operations
* 1,092 routing-induced additional 2Q operations
* 986 circuit depth
* 16 unique physical couplers

The corresponding SABRE implementation uses:
* 193 routed abstract 2Q operations
* 72 logical abstract 2Q operations
* 121 routing-induced additional 2Q operations
* Approximately 264–265 depth
* 8 unique physical couplers

Despite this much larger routing footprint, Prometheus wins several of the matched randomized $N=9$ instances. For the reported CrossEnt $N=9$ subset, Prometheus achieves an 80% matched-instance win rate (4/5) against SABRE O3. This is important because the routing penalty is not marginal; the comparison is occurring between physical implementations with dramatically different routing overhead.

---

## 7. Example: CrossEnt N=9, Instance 5
One matched instance illustrates the magnitude of the routing difference.

### Prometheus
| Metric | Value |
| :--- | :--- |
| **Physical depth** | 986 |
| **Logical abstract 2Q gates** | 72 |
| **Routed abstract 2Q gates** | 1,164 |
| **Routing-induced 2Q overhead** | 1,092 |
| **Unique physical couplers** | 16 |
| **Hellinger fidelity** | 0.745065 |
| **TVD** | 0.407166 |

### SABRE O3
| Metric | Value |
| :--- | :--- |
| **Physical depth** | 264 |
| **Logical abstract 2Q gates** | 72 |
| **Routed abstract 2Q gates** | 193 |
| **Routing-induced 2Q overhead** | 121 |
| **Unique physical couplers** | 8 |
| **Hellinger fidelity** | 0.643855 |
| **TVD** | 0.441354 |

The Prometheus implementation therefore uses:
* **971 additional routed 2Q operations**
* Approximately **3× the routed 2Q count**
* Substantially greater physical depth

Yet it produces a higher measured Hellinger fidelity:
$$0.745065 - 0.643855 = +0.101210$$
and a lower TVD:
$$0.441354 - 0.407166 = -0.034188$$

This is a particularly direct example of the regime under investigation: **Higher routing cost, but better measured agreement with the ideal logical distribution.** It should not, however, be interpreted as proof that the additional 971 operations themselves improve fidelity. The experiment only establishes the performance of the complete physical implementation.

---

## 8. Randomized Instance Results
The raw telemetry contains 120 compiler executions corresponding to the randomized benchmark set. Representative QAOA comparisons include:

| Instance | SABRE $F_H$ | Prometheus $F_H$ | Prometheus $\Delta F_H$ |
| :--- | :--- | :--- | :--- |
| **QAOA-6 Instance 1** | 0.901119 | 0.979869 | +0.078750 |
| **QAOA-6 Instance 2** | 0.893370 | 0.985036 | +0.091666 |
| **QAOA-6 Instance 3** | 0.781095 | 0.974398 | +0.193303 |
| **QAOA-6 Instance 4** | 0.955303 | 0.936174 | -0.019129 |
| **QAOA-6 Instance 5** | 0.892587 | 0.984268 | +0.091681 |
| **QAOA-8 Instance 1** | 0.881827 | 0.913069 | +0.031242 |
| **QAOA-8 Instance 2** | 0.829634 | 0.917668 | +0.088034 |
| **QAOA-8 Instance 3** | 0.689617 | 0.918525 | +0.228908 |
| **QAOA-8 Instance 4** | 0.723898 | 0.914222 | +0.190324 |
| **QAOA-8 Instance 5** | 0.880282 | 0.875870 | -0.004412 |
| **QAOA-9 Instance 1** | 0.542740 | 0.861710 | +0.318970 |
| **QAOA-9 Instance 2** | 0.922769 | 0.824543 | -0.098226 |
| **QAOA-9 Instance 3** | 0.926257 | 0.890177 | -0.036080 |
| **QAOA-9 Instance 4** | 0.882525 | 0.839242 | -0.043283 |
| **QAOA-9 Instance 5** | 0.708818 | 0.832364 | +0.123546 |

The table illustrates why the benchmark is framed as a regime study rather than a universal compiler ranking. Prometheus produces both large positive and negative deltas. That is expected. The scientific question is whether positive deltas occur repeatedly when Prometheus accepts substantially higher routing cost. They do.

---

## 9. Quadrant Analysis
The primary analysis compares $\Delta C_{\mathrm{routing}}$ against $\Delta F_H$. This produces four conceptually important regions:

* **Lower cost / higher fidelity:** Conventional optimization succeeds.
* **Higher cost / higher fidelity:** Hardware-aware routing tradeoff.
* **Lower cost / lower fidelity:** Lower routing cost does not guarantee better fidelity.
* **Higher cost / lower fidelity:** Routing penalty dominates.

The higher-cost / higher-fidelity quadrant is the primary target of this experiment. Its existence demonstrates that routing cost alone cannot fully predict which physical realization will produce the best measured result on the tested hardware. 

The higher-cost / lower-fidelity quadrant is equally important. It prevents the experiment from being interpreted as evidence that additional routing is intrinsically beneficial. As routing overhead becomes sufficiently large, the physical penalty can dominate.

---

## 10. The Scaling Boundary
The randomized data also demonstrates why the claim is deliberately limited. Prometheus does not win every matched instance. Examples include QAOA instances where SABRE retains higher measured fidelity, as well as dense CrossEnt cases where the additional routing overhead becomes dominant. 

This is consistent with the earlier deterministic scaling experiments:

| Workload | Lower-cost comparator | Prometheus | Observed regime |
| :--- | :--- | :--- | :--- |
| **QAOA-6** | Lower routing cost | Higher routing cost + higher fidelity | Benefit regime |
| **QAOA-9** | Lower routing cost | Higher routing cost + higher fidelity in selected instances | Mixed/benefit regime |
| **QAOA-10** | Lower routing cost | Higher routing cost + lower fidelity | Scaling boundary |
| **QFT-9** | Lower routing cost | Much higher routing cost + higher fidelity | Benefit regime |
| **QFT-10** | Lower routing cost | Much higher routing cost + lower fidelity | Scaling boundary |

The randomized benchmark therefore complements the earlier experiments rather than replacing them. The combined evidence suggests a workload- and scale-dependent operating regime, rather than a monotonic rule in either direction.

---

## 11. Physical Placement as a Candidate Mechanism
The results are consistent with the hypothesis that physical placement contributes materially to the observed differences. A real QPU is not spatially homogeneous. Different physical qubits and couplers can exhibit different:
* Gate error rates
* Readout error rates
* Coherence properties
* Calibration histories
* Connectivity relationships
* Susceptibility to accumulated error

Consequently, two physically different implementations of the same logical circuit can have substantially different observed distributions. The experiment therefore motivates treating routing as more than a pure shortest-path problem. A more complete conceptual objective is:

$$\text{execution quality} = f(\text{logical circuit}, \text{physical placement}, \text{routing}, \text{hardware state})$$

rather than assuming that execution quality can be adequately predicted from routing cost alone.

### Important Qualification
The present experiment does not independently quantify the causal contribution of individual qubit or coupler calibration parameters. The data therefore support physical placement as a candidate explanatory variable, not as an isolated causal proof.

---

## 12. What the Benchmark Supports
The randomized benchmark supports the following conclusions:
* The routing-cost/fidelity separation is observed across randomized logical instances rather than only a small set of manually selected circuits.
* Higher routing cost can coincide with substantially higher measured Hellinger fidelity.
* The effect occurs across multiple randomized QAOA and CrossEnt instances.
* The effect is not universal.
* The magnitude and direction of the effect depend on workload and problem size.
* Routing cost alone is therefore insufficient to predict measured fidelity in all tested cases.
* Physical placement is a plausible and experimentally relevant optimization variable.

---

## 13. What the Benchmark Does Not Establish
This experiment does not establish that:
* Prometheus is universally better than SABRE.
* Prometheus is universally better than TKET.
* Additional SWAP gates intrinsically improve fidelity.
* Circuit depth should be ignored.
* 2Q gate count is irrelevant.
* Physical placement is the sole cause of the observed differences.
* IBM Heron behavior necessarily generalizes to other processor generations.
* Randomized QAOA and CrossEnt workloads represent all quantum applications.
* A 75% win rate constitutes a universal statistical law.

The claim is deliberately narrower:
> Across the tested randomized QAOA and CrossEnt instances, Prometheus frequently achieved higher measured logical-distribution fidelity than SABRE O3 despite accepting substantially higher routing cost, demonstrating that the observed routing-cost/fidelity separation is not confined to a small set of manually selected circuits.

---

## 14. Data Integrity and Verification Status
The telemetry contains the following fields for each compiler execution:
* Hellinger fidelity
* Total Variation Distance
* Benchmark / instance identifier
* Compiler
* Physical circuit depth
* Execution order
* Logical abstract 2Q gate count
* Routed abstract 2Q gate count
* Routing-induced 2Q overhead
* Routing-induced SWAP count
* Semantic verification status
* Unique physical edges used

The current telemetry records the compiled circuits with `COMPILED_UNVERIFIED` for the `semantic_fidelity` field. This distinction is intentional. The reported Hellinger fidelity and TVD values are distributional measurements extracted from the hardware results. The `COMPILED_UNVERIFIED` status indicates that the separate semantic verification layer has not asserted full end-to-end semantic equivalence for those compiled artifacts.

The repository should therefore be read as an empirical hardware telemetry dataset, not as a claim that every compiler artifact has already passed an independent formal semantic-verification pipeline.

---

## 15. Independent Verification
The repository is designed to minimize the amount of trust required in the Prometheus implementation. The routing heuristics are not distributed. Instead, reviewers can inspect the resulting artifacts.

### Available Artifacts
```text
/data/
    telemetry
    summaries
    raw counts

/circuits/
    logical instances
    routed circuits
    native translated circuits
    execution payloads

/scripts/
    telemetry extraction
    metric calculation
    verification utilities
