#!/usr/bin/env python3
"""
===============================================================================
MATCHED-PAIR INFLECTION ANALYZER
Parses randomized benchmark data to evaluate Delta Fidelity vs Delta Routing Cost.
===============================================================================
"""

import csv
import re
from pathlib import Path
from collections import defaultdict

def get_latest_run_dir() -> Path:
    runs = sorted(Path(".").glob("crucible_run_*"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not runs:
        raise FileNotFoundError("No crucible_run directories found.")
    return runs[0]

def parse_benchmark_name(name: str):
    """Extracts family, N, and instance ID from strings like QAOA_8_INST_3"""
    match = re.match(r"([A-Za-z]+)_(\d+)_INST_(\d+)", name)
    if match:
        return match.group(1), int(match.group(2)), int(match.group(3))
    # Fallback for the older naming convention just in case
    parts = name.split("_")
    return parts[0], int(parts[1]), 0

def load_matched_records(csv_path: Path):
    raw_data = defaultdict(dict)
    
    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            bench = row["benchmark"]
            comp = row["compiler"]
            
            raw_data[bench][comp] = {
                "fidelity": float(row.get("Hellinger_fidelity", 0.0) or 0.0),
                "routed_2q": int(float(row.get("routed_abstract_2q_gates", 0) or 0)),
                "depth": int(float(row.get("depth", 0) or 0)),
                "swaps": int(float(row.get("routing_induced_swaps", 0) or 0))
            }
    
    return raw_data

def main():
    run_dir = get_latest_run_dir()
    csv_path = run_dir / "results" / "summary.csv"
    print(f"Analyzing dataset: {csv_path.parent.parent.name}\n")
    
    records = load_matched_records(csv_path)
    
    # Structure: stats[family][N] = list of deltas
    stats = defaultdict(lambda: defaultdict(list))
    
    for bench, comps in records.items():
        if "PROMETHEUS" in comps and "SABRE_O3" in comps:
            fam, n, inst = parse_benchmark_name(bench)
            
            prom = comps["PROMETHEUS"]
            sabre = comps["SABRE_O3"]
            
            delta_fid = prom["fidelity"] - sabre["fidelity"]
            delta_2q = prom["routed_2q"] - sabre["routed_2q"]
            
            stats[fam][n].append({
                "inst": inst,
                "bench": bench,
                "delta_fid": delta_fid,
                "delta_2q": delta_2q,
                "prom_fid": prom["fidelity"],
                "sabre_fid": sabre["fidelity"]
            })

    # Output the Inflection Surface Analysis
    print("=" * 115)
    print(f"{'FAMILY':<10} | {'N':<3} | {'INSTANCES':<10} | {'PROM WIN RATE':<15} | {'AVG Δ COST (2Q)':<18} | {'AVG Δ FIDELITY':<15}")
    print("=" * 115)

    for fam in sorted(stats.keys()):
        for n in sorted(stats[fam].keys()):
            instances = stats[fam][n]
            count = len(instances)
            
            prom_wins = sum(1 for x in instances if x["delta_fid"] > 0)
            win_rate = (prom_wins / count) * 100
            
            avg_delta_cost = sum(x["delta_2q"] for x in instances) / count
            avg_delta_fid = sum(x["delta_fid"] for x in instances) / count
            
            # Format color/status based on win rate and fidelity
            if win_rate > 50 and avg_delta_fid > 0:
                trend = "YIELD ADVANTAGE"
            elif win_rate < 50 and avg_delta_fid < 0:
                trend = "COHERENCE DEATH"
            else:
                trend = "MARGINAL / TIE"
                
            print(f"{fam:<10} | {n:<3} | {count:<10} | {prom_wins}/{count} ({win_rate:>5.1f}%) | {avg_delta_cost:>+10.2f} gates   | {avg_delta_fid:>+10.4f}      | {trend}")
        print("-" * 115)
        
    # Quadrant Analysis (The actual routing thesis check)
    print("\n" + "=" * 80)
    print("ROUTING COST VS. HARDWARE YIELD QUADRANT ANALYSIS")
    print("=" * 80)
    print("Testing Hypothesis: Does taking a higher 2Q/SWAP penalty systematically")
    print("improve fidelity by avoiding bad physical edge crosstalk?\n")
    
    quadrants = {"paid_off": 0, "free_lunch": 0, "coherence_death": 0, "strictly_worse": 0}
    total_analyzed = 0
    
    for fam in stats:
        for n in stats[fam]:
            for x in stats[fam][n]:
                total_analyzed += 1
                cost_higher = x["delta_2q"] > 0
                fid_better = x["delta_fid"] > 0
                
                if cost_higher and fid_better:
                    quadrants["paid_off"] += 1       # Prom took more gates, got better fidelity
                elif not cost_higher and fid_better:
                    quadrants["free_lunch"] += 1     # Prom took fewer/equal gates, got better fidelity
                elif cost_higher and not fid_better:
                    quadrants["coherence_death"] += 1 # Prom took more gates, noise destroyed fidelity
                else:
                    quadrants["strictly_worse"] += 1  # Prom took fewer gates, got worse fidelity
                    
    print(f"Total Matched Instances Analyzed: {total_analyzed}")
    print(f"  1. The Investment Paid Off (Higher Cost, Better Yield):  {quadrants['paid_off']:<3} instances ({(quadrants['paid_off']/total_analyzed)*100:.1f}%)")
    print(f"  2. Coherence Death         (Higher Cost, Worse Yield) :  {quadrants['coherence_death']:<3} instances ({(quadrants['coherence_death']/total_analyzed)*100:.1f}%)")
    print(f"  3. Free Lunch              (Lower Cost, Better Yield) :  {quadrants['free_lunch']:<3} instances ({(quadrants['free_lunch']/total_analyzed)*100:.1f}%)")
    print(f"  4. Strictly Worse          (Lower Cost, Worse Yield)  :  {quadrants['strictly_worse']:<3} instances ({(quadrants['strictly_worse']/total_analyzed)*100:.1f}%)")
    print("=" * 80)

if __name__ == "__main__":
    main()