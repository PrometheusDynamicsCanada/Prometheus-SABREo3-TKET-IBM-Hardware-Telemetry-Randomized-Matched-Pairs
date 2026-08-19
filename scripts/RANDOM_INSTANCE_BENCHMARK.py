#!/usr/bin/env python3
"""
===============================================================================
RANDOMIZED INSTANCE BENCHMARK (N=6 to 9)
Tests Prometheus vs SABRE_O3 vs TKET across matched randomized instances.

- QAOA: Randomized Erdos-Renyi graph topologies.
- CrossEnt: Randomized parameters on fixed full-entanglement topologies.

Includes strict edge-invariance contracts, target instruction compliance, 
explicit SWAP telemetry decomposition, and a fatal 120/120 matrix check.
===============================================================================
"""

from __future__ import annotations

import os
import sys
import time
import json
import random
import math
import hashlib
import platform
import traceback
from pathlib import Path
from datetime import datetime, timezone
from collections import Counter
from dataclasses import dataclass, asdict
from typing import Any, Dict, Iterable, List, Optional, Tuple

import numpy as np

try:
    import h5py
    HAS_HDF5 = True
except ImportError:
    HAS_HDF5 = False
    print("[WARNING] h5py not found. .hdf5 telemetry export will be skipped.")

# ---------------------------------------------------------------------------
# Qiskit
# ---------------------------------------------------------------------------
import qiskit
from qiskit import QuantumCircuit, transpile, qasm2
from qiskit.quantum_info import Statevector
from qiskit.converters import circuit_to_dag, dag_to_circuit
from qiskit.transpiler import PassManager
from qiskit.transpiler.passes import BasisTranslator, UnrollCustomDefinitions
from qiskit.circuit.equivalence_library import SessionEquivalenceLibrary as sel
from qiskit.circuit.library import n_local
import qiskit_ibm_runtime
from qiskit_ibm_runtime import QiskitRuntimeService, SamplerV2 as Sampler

# ---------------------------------------------------------------------------
# Optional TKET
# ---------------------------------------------------------------------------
try:
    import pytket
    from pytket.extensions.qiskit import qiskit_to_tk, tk_to_qiskit
    from pytket.architecture import Architecture
    from pytket.passes import FullPeepholeOptimise, RoutingPass
    from pytket.placement import LinePlacement
    import re
    HAS_TKET = True
except Exception as e:
    print(f"\n[FATAL IMPORT ERROR - TKET]: {e}")
    pytket = None
    HAS_TKET = False

# ---------------------------------------------------------------------------
# Prometheus
# ---------------------------------------------------------------------------
try:
    from prometheus_v15 import optimize as prometheus_optimize
    PROMETHEUS_VERSION = "15.x-local"
    HAS_PROMETHEUS = True
except Exception:
    prometheus_optimize = None
    PROMETHEUS_VERSION = "NOT_AVAILABLE"
    HAS_PROMETHEUS = False

# =============================================================================
# CONFIGURATION 
# =============================================================================

BACKEND_NAME = os.environ.get("CRUCIBLE_BACKEND", "ibm_marrakesh")
SHOTS = 4096
SEED = int(os.environ.get("CRUCIBLE_SEED", "20260817"))
SEMANTIC_THRESHOLD = float(os.environ.get("CRUCIBLE_SEMANTIC_THRESHOLD", "0.99"))

INSTANCES_PER_N = 5
TARGET_SIZES = [6, 7, 8, 9]
COMPILERS = ("SABRE_O3", "TKET", "PROMETHEUS")

# =============================================================================
# DATA MODEL
# =============================================================================

@dataclass
class RunRecord:
    benchmark: str
    compiler: str
    status: str = "UNSET"
    compile_time_sec: float = 0.0
    mapping_source: str = ""
    routing_topology: str = ""
    logical_to_physical_map: Optional[List[int]] = None
    source_wire_to_physical_map: Optional[Dict[str, int]] = None
    semantic_fidelity: Optional[float] = None
    hashes: Optional[Dict[str, str]] = None
    routed_metrics: Optional[Dict[str, Any]] = None
    final_executable_metrics: Optional[Dict[str, Any]] = None
    edge_sets: Optional[Dict[str, Any]] = None
    hardware_metrics: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    legalized_circuit_obj: Any = None

# =============================================================================
# UTILITIES
# =============================================================================

def hash_data(data: str) -> str:
    return hashlib.sha256(data.encode("utf-8")).hexdigest()

def hash_circuit(circuit: QuantumCircuit) -> Tuple[str, str]:
    try:
        text = qasm2.dumps(circuit)
    except Exception:
        from qiskit import qasm3
        text = qasm3.dumps(circuit)
    return hash_data(text), text

def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")

def serialize_environment(backend, out_dir: Path) -> str:
    env = {
        "qiskit": qiskit.__version__,
        "qiskit_ibm_runtime": qiskit_ibm_runtime.__version__,
        "backend": BACKEND_NAME,
        "backend_num_qubits": backend.num_qubits,
    }
    text = json.dumps(env, indent=2, sort_keys=True)
    write_text(out_dir / "env.json", text)
    return hash_data(text)

# =============================================================================
# RANDOMIZED BENCHMARK CIRCUITS
# =============================================================================

def generate_random_qaoa(n: int, instance_id: int) -> QuantumCircuit:
    """Randomized Erdos-Renyi Graph Topology."""
    random.seed(instance_id)
    edges = set()
    for i in range(n):
        for j in range(i + 1, n):
            if random.random() < 0.4:
                edges.add((i, j))
    
    if len(edges) < n:
        edges = set([(i, (i + 1) % n) for i in range(n)]) 
        
    qc = QuantumCircuit(n, n, name=f"QAOA_{n}_INST_{instance_id}")
    qc.h(range(n))
    gamma, beta = 0.392, 0.785
    
    for u, v in edges: 
        qc.rzz(2 * gamma, u, v)
    for i in range(n): 
        qc.rx(2 * beta, i)
        
    qc.measure(range(n), range(n))
    return qc

def generate_random_cross_ent(n: int, instance_id: int) -> QuantumCircuit:
    """Fixed Dense Topology, Randomized Parameters."""
    qc = n_local(
        num_qubits=n, 
        rotation_blocks=['rx', 'ry'], 
        entanglement_blocks='cx', 
        entanglement='full', 
        reps=2, 
        parameter_prefix=f'p_{instance_id}'
    ).decompose()
    
    random.seed(instance_id)
    rand_params = [random.uniform(0, 3.14159) for _ in range(qc.num_parameters)]
    qc = qc.assign_parameters(rand_params)
    
    qc.name = f"CrossEnt_{n}_INST_{instance_id}"
    qc.measure_all()
    return qc

# =============================================================================
# EXACT DISTRIBUTION / SEMANTIC CHECKING
# =============================================================================

def extract_measured_distribution(circuit: QuantumCircuit) -> Dict[str, float]:
    if circuit.num_clbits == 0: return {}
    q_index = {q: circuit.find_bit(q).index for q in circuit.qubits}
    meas_map = {q_index[inst.qubits[0]]: circuit.find_bit(inst.clbits[0]).index for inst in circuit.data if inst.operation.name == "measure"}
    dag = circuit_to_dag(circuit)
    active_qubits = [q for q in dag.qubits if dag.nodes_on_wire(q, only_ops=True) or q_index[q] in meas_map]
    active_indices = [q_index[q] for q in active_qubits]
    for q in list(dag.qubits):
        if q not in active_qubits: dag.remove_qubits(q)
    dag.remove_all_ops_named("measure")
    dag.remove_all_ops_named("barrier")
    active = dag_to_circuit(dag)
    sv = Statevector(active)
    probs = sv.probabilities_dict()
    out: Dict[str, float] = {}
    ncl = circuit.num_clbits
    for bitstr, p in probs.items():
        if p < 1e-15: continue
        classical = ["0"] * ncl
        for active_idx, val in enumerate(reversed(bitstr)):
            phys_q = active_indices[active_idx]
            if phys_q in meas_map: classical[ncl - 1 - meas_map[phys_q]] = val
        key = "".join(classical)
        out[key] = out.get(key, 0.0) + float(p)
    return out

def semantic_fidelity(
    logical_circuit: QuantumCircuit,
    canonical_physical_circuit: QuantumCircuit,
    logical_to_physical: List[int],
    ideal_dist: Dict[str, float],
) -> float:
    if logical_circuit.num_qubits > 16: return None
    dag = circuit_to_dag(canonical_physical_circuit)
    original_q_index = {q: canonical_physical_circuit.find_bit(q).index for q in dag.qubits}
    dag.remove_all_ops_named("measure")
    dag.remove_all_ops_named("barrier")
    active_qubits = [q for q in dag.qubits if dag.nodes_on_wire(q, only_ops=True)]
    active_physical_indices = [original_q_index[q] for q in active_qubits]
    if len(active_qubits) > 16: return None
    for q in list(dag.qubits):
        if q not in active_qubits: dag.remove_qubits(q)
    active = dag_to_circuit(dag)
    if active.num_qubits == 0: return 1.0 if not ideal_dist else 0.0
    sv = Statevector(active)
    probs = sv.probabilities_dict()
    physical_to_logical = {p: i for i, p in enumerate(logical_to_physical)}
    logical_probs: Dict[str, float] = {}
    for bitstr, probability in probs.items():
        if probability < 1e-15: continue
        logical_bits = ["0"] * logical_circuit.num_qubits
        for active_idx, bit_val in enumerate(reversed(bitstr)):
            pidx = active_physical_indices[active_idx]
            if pidx in physical_to_logical: logical_bits[logical_circuit.num_qubits - 1 - physical_to_logical[pidx]] = bit_val
        key = "".join(logical_bits)
        logical_probs[key] = logical_probs.get(key, 0.0) + float(probability)
    keys = set(ideal_dist) | set(logical_probs)
    bc = sum(math.sqrt(ideal_dist.get(k, 0.0) * logical_probs.get(k, 0.0)) for k in keys)
    return round(bc * bc, 6)

# =============================================================================
# TARGET / TOPOLOGY CONTRACTS
# =============================================================================

def validate_mapping_contract(
    logical_circuit: QuantumCircuit,
    canonical_circuit: QuantumCircuit,
    logical_to_physical: Optional[List[int]],
) -> None:
    if logical_to_physical is None:
        raise RuntimeError("Missing logical_to_physical placement.")
    if len(logical_to_physical) != logical_circuit.num_qubits:
        raise RuntimeError("Logical-to-physical map length does not equal logical qubit count.")
    if len(set(logical_to_physical)) != len(logical_to_physical):
        raise RuntimeError("Logical-to-physical map is not injective.")
    if not all(0 <= p < canonical_circuit.num_qubits for p in logical_to_physical):
        raise RuntimeError("Logical-to-physical map contains invalid coordinates.")

def target_edges(backend) -> set[Tuple[int, int]]:
    edges = set()
    if getattr(backend, "coupling_map", None):
        for q0, q1 in backend.coupling_map.get_edges():
            edges.add(tuple(sorted((q0, q1))))
    for _, qargs in backend.target.instructions:
        if qargs is not None and len(qargs) == 2:
            edges.add(tuple(sorted(qargs)))
    return edges

def validate_routing_contract(circuit: QuantumCircuit, backend) -> None:
    valid_edges = target_edges(backend)
    for inst in circuit.data:
        if len(inst.qubits) != 2:
            continue
        q0 = circuit.find_bit(inst.qubits[0]).index
        q1 = circuit.find_bit(inst.qubits[1]).index
        edge = tuple(sorted((q0, q1)))
        if edge not in valid_edges:
            raise RuntimeError(
                f"Routing contract failed: {inst.operation.name}{q0,q1} "
                f"uses unsupported physical edge."
            )

def validate_native_contract(circuit: QuantumCircuit, backend) -> None:
    ignored = {"barrier", "delay", "measure"}
    for inst in circuit.data:
        if inst.operation.name in ignored:
            continue
        qargs = tuple(circuit.find_bit(q).index for q in inst.qubits)
        if not backend.target.instruction_supported(inst.operation.name, qargs):
            raise RuntimeError(
                f"Native contract failed: {inst.operation.name}{qargs} unsupported by target."
            )

def validate_canonical_width(circuit: QuantumCircuit, backend) -> None:
    if circuit.num_qubits != backend.num_qubits:
        raise RuntimeError(
            f"Canonical width mismatch: {circuit.num_qubits} != {backend.num_qubits}"
        )

def extract_physical_edge_multiset(circuit: QuantumCircuit) -> Counter[Tuple[int, int]]:
    edges: Counter = Counter()
    for inst in circuit.data:
        if len(inst.qubits) == 2:
            q0 = circuit.find_bit(inst.qubits[0]).index
            q1 = circuit.find_bit(inst.qubits[1]).index
            edges[tuple(sorted((q0, q1)))] += 1
    return edges

def build_canonical_circuit(routed_temp: QuantumCircuit, source_qubit_to_physical: Dict[Any, int], backend, num_clbits: int) -> QuantumCircuit:
    canonical = QuantumCircuit(backend.num_qubits, num_clbits, name=routed_temp.name)
    q_map = {src_q: canonical.qubits[phys_idx] for src_q, phys_idx in source_qubit_to_physical.items()}
    c_index = {src_c: i for i, src_c in enumerate(routed_temp.clbits)}
    for inst in routed_temp.data:
        qargs = [q_map[q] for q in inst.qubits]
        cargs = [canonical.clbits[c_index[c]] for c in inst.clbits]
        canonical.append(inst.operation, qargs, cargs)
    return canonical

# =============================================================================
# COMPILERS
# =============================================================================

def compile_sabre(logical_circuit: QuantumCircuit, backend) -> Dict[str, Any]:
    start = time.perf_counter()
    routed = transpile(logical_circuit, target=backend.target, optimization_level=3, routing_method="sabre", layout_method="sabre", seed_transpiler=SEED)
    final_map = list(routed.layout.final_index_layout(filter_ancillas=True))
    source_map = {q: routed.find_bit(q).index for q in routed.qubits}
    return {"circuit": routed, "logical_to_physical": final_map, "source_to_physical": source_map, "mapping_source": "qiskit_layout", "routing_topology": "SABRE O3", "compile_time_sec": time.perf_counter() - start}

def compile_tket(logical_circuit: QuantumCircuit, backend) -> Dict[str, Any]:
    start = time.perf_counter()
    tk_logical = transpile(logical_circuit, basis_gates=['cx', 'id', 'rz', 'sx', 'x'], optimization_level=1)
    tk_circ = qiskit_to_tk(tk_logical)
    logical_qubits = list(tk_circ.qubits)
    edges = backend.coupling_map.get_edges() if getattr(backend, "coupling_map", None) else []
    architecture = Architecture(edges)
    FullPeepholeOptimise().apply(tk_circ)
    placement = LinePlacement(architecture)
    placement_map = placement.get_placement_map(tk_circ)
    placement.place(tk_circ)
    RoutingPass(architecture).apply(tk_circ)
    permutation = tk_circ.implicit_qubit_permutation()
    final_map = []
    for lq in logical_qubits:
        initial_node = placement_map[lq]
        final_node = permutation.get(initial_node, initial_node)
        final_map.append(int(final_node.index[0]))
    routed_qiskit = tk_to_qiskit(tk_circ)
    source_map = {}
    for q in routed_qiskit.qubits:
        m = re.search(r'index=(\d+)', repr(q))
        if m: source_map[q] = int(m.group(1))
        else: source_map[q] = int("".join(ch for ch in str(q) if ch.isdigit()))
    canonical = build_canonical_circuit(routed_qiskit, source_map, backend, logical_circuit.num_clbits)
    return {"circuit": canonical, "logical_to_physical": final_map, "source_to_physical": source_map, "mapping_source": "TKET LinePlacement", "routing_topology": "TKET Arch", "compile_time_sec": time.perf_counter() - start}

def compile_prometheus(logical_circuit: QuantumCircuit, backend) -> Dict[str, Any]:
    start = time.perf_counter()
    result = prometheus_optimize(logical_circuit.copy(), backend=backend, return_mapping=True)
    routed_temp = result.get("circuit")
    logical_map = result.get("logical_to_physical_final")
    source_map = result.get("source_qubit_to_physical_map")
    canonical = build_canonical_circuit(routed_temp, source_map, backend, logical_circuit.num_clbits)
    return {"circuit": canonical, "logical_to_physical": [int(p) for p in logical_map], "source_to_physical": source_map, "mapping_source": "Prometheus", "routing_topology": "Prometheus", "compile_time_sec": time.perf_counter() - start}

COMPILER_FUNCS = {
    "SABRE_O3": compile_sabre,
    "TKET": compile_tket,
    "PROMETHEUS": compile_prometheus,
}

# =============================================================================
# COMMON POST-ROUTING TRANSLATION
# =============================================================================

def common_hardware_translate(routed_canonical: QuantumCircuit, backend) -> QuantumCircuit:
    pm = PassManager([
        UnrollCustomDefinitions(sel, target=backend.target),
        BasisTranslator(sel, list(backend.target.operation_names), target=backend.target),
    ])
    translated = pm.run(routed_canonical)
    translated.name = routed_canonical.name
    return translated

# =============================================================================
# METRICS
# =============================================================================

def circuit_metrics(circuit: QuantumCircuit, backend=None) -> Dict[str, Any]:
    ignored = {"measure", "barrier", "delay"}
    one_q = 0
    two_q_total = 0
    swaps = 0
    two_q_non_swap = 0
    gate_count = 0
    
    for inst in circuit.data:
        if inst.operation.name in ignored: continue
        gate_count += 1
        nq = len(inst.qubits)
        if nq == 1: 
            one_q += 1
        elif nq == 2:
            two_q_total += 1
            if inst.operation.name == "swap":
                swaps += 1
            else:
                two_q_non_swap += 1
                
    two_q_depth = circuit.depth(filter_function=lambda x: len(x.qubits) > 1 and x.operation.name not in ignored)
    
    return {
        "gate_count": gate_count,
        "1q_gates": one_q,
        "abstract_2q_operations": two_q_total,
        "explicit_swaps": swaps,
        "two_q_non_swap": two_q_non_swap,
        "unique_2q_edge_count": len(extract_physical_edge_multiset(circuit)),
        "depth": circuit.depth(),
        "two_qubit_depth": two_q_depth,
    }

def distribution_metrics(counts: Dict[str, int], ideal: Dict[str, float], shots: int) -> Dict[str, Any]:
    empirical = {k: v / shots for k, v in counts.items() if v > 0}
    keys = set(empirical) | set(ideal)
    tvd = 0.5 * sum(abs(empirical.get(k, 0.0) - ideal.get(k, 0.0)) for k in keys)
    bc = sum(math.sqrt(empirical.get(k, 0.0) * ideal.get(k, 0.0)) for k in keys)
    hellinger_fidelity = bc * bc
    return {
        "TVD": round(tvd, 6),
        "Hellinger_fidelity": round(hellinger_fidelity, 6)
    }

# =============================================================================
# EXECUTION & RESULTS
# =============================================================================

def compile_arm(
    compiler_name: str,
    benchmarks: Dict[str, QuantumCircuit],
    ideals: Dict[str, Dict[str, float]],
    backend,
    root: Path,
) -> List[RunRecord]:
    fn = COMPILER_FUNCS[compiler_name]
    records: List[RunRecord] = []
    
    for benchmark_name, logical in benchmarks.items():
        print(f"    {compiler_name:12s} -> {benchmark_name}")
        try:
            compiled = fn(logical, backend)
            canonical = compiled["circuit"]
            canonical.name = f"{benchmark_name}__{compiler_name}"
            
            # Structural & Routing Validation
            validate_canonical_width(canonical, backend)
            logical_map = compiled["logical_to_physical"]
            validate_mapping_contract(logical, canonical, logical_map)
            validate_routing_contract(canonical, backend)

            # Semantic Validation (Exact Statevector for N <= 9)
            sem_fid = semantic_fidelity(logical, canonical, logical_map, ideals[benchmark_name])
            if sem_fid is None: record_status = "COMPILED_UNVERIFIED"
            elif sem_fid < SEMANTIC_THRESHOLD: raise RuntimeError(f"Semantic fidelity {sem_fid:.6f} < threshold.")
            else: record_status = "COMPILED_SEMANTIC_PASS"

            logical_metrics = circuit_metrics(logical)
            routed_metrics = circuit_metrics(canonical)
            routed_edges = extract_physical_edge_multiset(canonical)
            
            # Write Routed QASM
            r_hash, r_qasm = hash_circuit(canonical)
            write_text(root / "routed" / f"{benchmark_name}__{compiler_name}.qasm", r_qasm)

            # Translation & Topology Invariance Check
            translated = common_hardware_translate(canonical, backend)
            translated.name = canonical.name
            translated_edges = extract_physical_edge_multiset(translated)
            
            if not set(translated_edges).issubset(set(routed_edges)):
                raise RuntimeError("Common translation introduced a new physical two-qubit edge, violating routing topology.")
            
            validate_native_contract(translated, backend)
            
            # Write Translated QASM
            t_hash, t_qasm = hash_circuit(translated)
            write_text(root / "translated" / f"{benchmark_name}__{compiler_name}.qasm", t_qasm)

            final_metrics = circuit_metrics(translated, backend)
            
            # Fully Populated Record for Auditing
            records.append(RunRecord(
                benchmark=benchmark_name,
                compiler=compiler_name,
                status=record_status,
                compile_time_sec=round(compiled["compile_time_sec"], 6),
                mapping_source=compiled["mapping_source"],
                routing_topology=compiled["routing_topology"],
                logical_to_physical_map=[int(x) for x in logical_map],
                source_wire_to_physical_map={
                    str(k): int(v) for k, v in compiled["source_to_physical"].items()
                },
                semantic_fidelity=sem_fid,
                hashes={
                    "routed": r_hash,
                    "translated": t_hash,
                },
                routed_metrics=routed_metrics,
                final_executable_metrics={
                    "logical_abstract_2q_gates": logical_metrics["abstract_2q_operations"],
                    "routed_abstract_2q_gates": routed_metrics["abstract_2q_operations"],
                    "routing_induced_swaps": routed_metrics["explicit_swaps"] - logical_metrics["explicit_swaps"],
                    "routing_induced_2q_overhead": routed_metrics["abstract_2q_operations"] - logical_metrics["abstract_2q_operations"],
                    "unique_physical_edges_used": routed_metrics["unique_2q_edge_count"],
                    "depth": final_metrics["depth"],
                },
                edge_sets={
                    "routed_physical_edges_counts": dict(routed_edges),
                    "translated_physical_edges_counts": dict(translated_edges),
                },
                legalized_circuit_obj=translated,
            ))
        except Exception as exc:
            records.append(RunRecord(benchmark=benchmark_name, compiler=compiler_name, status="COMPILE_OR_VALIDATION_FAIL", error=str(exc)))
            print(f"        [FAIL] {compiler_name} / {benchmark_name}: {exc}")
    return records


def decode_counts(pub_result) -> Dict[str, int]:
    if hasattr(pub_result.data, "meas"): return pub_result.data.meas.get_counts()
    if hasattr(pub_result.data, "c"): return pub_result.data.c.get_counts()
    raise RuntimeError("Unable to locate measurement BitArray.")

def submit_unified_job(valid_records: List[RunRecord], backend):
    rng = random.Random(SEED)
    rng.shuffle(valid_records)
    payload = [r.legalized_circuit_obj for r in valid_records]
    execution_order = [f"{i}: {r.compiler} / {r.benchmark}" for i, r in enumerate(valid_records)]

    sampler = Sampler(mode=backend)
    sampler.options.dynamical_decoupling.enable = False
    sampler.options.twirling.enable_gates = False
    sampler.options.twirling.enable_measure = False

    job = sampler.run(payload, shots=SHOTS)
    return job, execution_order

def process_unified_result(job, valid_records, ideals, root: Path):
    results = job.result()
    raw_counts = {}
    summary_rows = []
    for idx, record in enumerate(valid_records):
        counts = decode_counts(results[idx])
        key = f"{record.benchmark}__{record.compiler}"
        raw_counts[key] = counts
        metrics = distribution_metrics(counts, ideals[record.benchmark], SHOTS)
        record.hardware_metrics = {**metrics, "execution_index": idx}
        summary_rows.append({
            "execution_index": idx,
            "benchmark": record.benchmark,
            "compiler": record.compiler,
            "status": record.status,
            "semantic_fidelity": record.semantic_fidelity,
            **(record.final_executable_metrics or {}),
            **record.hardware_metrics,
        })

    import csv
    flat_path = root / "results" / "summary.csv"
    fieldnames = sorted({k for row in summary_rows for k in row.keys()})
    with flat_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(summary_rows)

    if HAS_HDF5:
        with h5py.File(root / "results" / "telemetry.hdf5", "w") as f:
            counts_grp = f.create_group("raw_counts")
            for key, counts in raw_counts.items():
                ds = counts_grp.create_dataset(key, data=list(counts.values()))
                ds.attrs["keys"] = json.dumps(list(counts.keys()))
            metrics_grp = f.create_group("metrics")
            for row in summary_rows:
                row_grp = metrics_grp.create_group(f"{row['benchmark']}__{row['compiler']}")
                for k, v in row.items():
                    if isinstance(v, (int, float, str)): row_grp.attrs[k] = v
        print("    -> Extensive telemetry written to telemetry.hdf5")
    return raw_counts, summary_rows

def main():
    print("=" * 90)
    print("RANDOMIZED SCALING MATRIX — 3-WAY UNIFIED COMPILER BENCHMARK")
    print("=" * 90)
    
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    root = Path(f"crucible_run_{stamp}")
    for sub in ("environment", "results", "routed", "translated"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    
    service = QiskitRuntimeService(channel="ibm_quantum_platform")
    backend = service.backend(BACKEND_NAME)
    serialize_environment(backend, root / "environment")

    print("[INFO] Building Randomized Job Queue...")
    BENCHMARKS = {}
    ideals = {}
    
    for n in TARGET_SIZES:
        for inst in range(1, INSTANCES_PER_N + 1):
            qc_qaoa = generate_random_qaoa(n, inst)
            BENCHMARKS[qc_qaoa.name] = qc_qaoa
            ideals[qc_qaoa.name] = extract_measured_distribution(qc_qaoa)
            
            qc_ce = generate_random_cross_ent(n, inst)
            BENCHMARKS[qc_ce.name] = qc_ce
            ideals[qc_ce.name] = extract_measured_distribution(qc_ce)

    expected = len(BENCHMARKS) * len(COMPILERS)
    print(f"[INFO] Logical circuits: {len(BENCHMARKS)}")
    print(f"[INFO] Expected compiler outputs: {expected}")

    all_records: List[RunRecord] = []
    print("\n[1] LOCAL COMPILATION / ROUTING / CONTRACT AUDIT\n")
    for compiler_name in COMPILERS:
        arm = compile_arm(compiler_name, BENCHMARKS, ideals, backend, root)
        all_records.extend(arm)

    # =============================================================================
    # FATAL MATRIX CHECK 
    # =============================================================================
    valid_records = [
        r for r in all_records 
        if r.status in {"COMPILED_SEMANTIC_PASS", "COMPILED_UNVERIFIED"} 
        and r.legalized_circuit_obj is not None
    ]
    
    if len(valid_records) != expected:
        failures = [
            (r.compiler, r.benchmark, r.status, r.error)
            for r in all_records if r not in valid_records
        ]
        raise RuntimeError(
            f"\n[FATAL] FULL MATRIX REQUIRED: {len(valid_records)}/{expected}\n"
            f"Failures preventing matched-pair execution:\n{json.dumps(failures, indent=2)}"
        )

    print("\n[2] SINGLE UNIFIED IBM RUNTIME JOB\n")
    job, execution_order = submit_unified_job(valid_records, backend)
    print(f"Job ID: {job.job_id()}")
    print(f"PUB count: {len(valid_records)}")

    terminal = {"DONE", "ERROR", "CANCELLED"}
    while job.status() not in terminal:
        print(f"\rStatus: {job.status()}", end="", flush=True)
        time.sleep(5)
    print()

    if job.status() != "DONE":
        raise RuntimeError(f"Job failed with status {job.status()}")

    print("\n[3] PROCESSING HARDWARE RESULTS\n")
    process_unified_result(job, valid_records, ideals, root)
    print("\n[✓] COMPLETE")
    print(f"Artifacts: {root}")

if __name__ == "__main__":
    main()