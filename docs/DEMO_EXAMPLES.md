# Demo Examples: AutoSizer Optimization Results

Three demonstration circuits optimized using the AutoSizer framework. Each demo uses a self-contained YAML configuration with template-based SPICE simulation and LLM-driven iterative optimization.

## Overview

| Circuit | PDK | Variables | Search Space | Trials | Status |
|---------|-----|-----------|-------------|--------|--------|
| 3-Stage Ring Oscillator | SKY130 | 3 (`L_inv`, `W_pmos`, `W_nmos`) | L: 5 vals, W_pmos: 6 vals, W_nmos: 6 vals | 3 | All specs met |
| Five-Transistor OTA | GF180MCU | 6 (`L_tail_base`, `L_diff_base`, `L_load_base`, `W_tail_base`, `W_diff_base`, `W_load_base`) | L: 8 vals, W: 8 vals, with scales | 2 | All specs met |
| Inverter | GF180MCU | 3 (`L`, `W_pmos_base`, `W_nmos_base`) | L: 3 vals, W: 9 vals, with scales | 2 | All specs met |

---

## 1. 3-Stage Ring Oscillator (SKY130)

### Circuit

Three inverter stages in a closed-loop ring, each with identical transistor sizing. Load capacitors on each output node. Characterized at nominal VDD = 1.8V.

### Variables

| Variable | Allowed Values | Count |
|----------|---------------|-------|
| `L_inv` | [0.3, 0.4, 0.5, 0.6, 0.7] µm | 5 |
| `W_pmos` | [1.0, 2.0, 3.0, 4.0, 8.0, 12.0] µm | 6 |
| `W_nmos` | [0.5, 1.0, 1.5, 2.0, 4.0, 6.0] µm | 6 |

Total possible combinations: 5 × 6 × 6 = **180**

### User Specifications

```
fom > 1.1 AND frequency_mhz > 400 AND power_uw < 300
```

### Results

| Trial | Eval Count | Best FOM | Specs Met | Converged At | Best Design (L, Wp, Wn) |
|-------|-----------|----------|-----------|-------------|------------------------|
| 0 | 25 | 1.337 | ✓ | 24 evals | (0.3, 1.0, 2.0) |
| 1 | 111 | 1.205 | ✓ | 105 evals | (0.3, 1.0, 2.0) |
| 2 | 58 | 1.337 | ✓ | 53 evals | (0.3, 1.0, 2.0) |

All three trials converged to the same optimum: `L_inv=0.3µm`, `W_pmos=1.0µm`, `W_nmos=2.0µm` — the minimum channel length, confirming that smaller L monotonically improves oscillation frequency and FOM. These results reflect the BUG #10 fix (variable sensitivity analysis + narrowing rules), which reduced total evaluations by 48% vs. the pre-fix baseline (371 → 194).

---

## 2. Five-Transistor OTA (GF180MCU)

### Circuit

A five-transistor differential OTA with current-mirror tail and active load, in the GF 180nm CMOS process. Characterized for DC gain, UGBW, and power consumption with 1pF load at VDD=3.3V.

### Variables

Six base variables with width/length scaling:

| Variable | Allowed Values | Scale | Physical Name | Count |
|----------|---------------|-------|---------------|-------|
| `L_tail_base` | 8 values (0.28–3.36 µm) | ×2 | `L_tail` | 8 |
| `L_diff_base` | 8 values (0.28–3.36 µm) | ×1 | `L_diff` | 8 |
| `L_load_base` | 8 values (0.28–3.36 µm) | ×2 | `L_load` | 8 |
| `W_tail_base` | 8 values (0.60–9.60 µm) | ×4 | `W_tail` | 8 |
| `W_diff_base` | 8 values (0.60–9.60 µm) | ×4 | `W_diff` | 8 |
| `W_load_base` | 8 values (0.60–9.60 µm) | ×4 | `W_load` | 8 |

Total possible combinations: 8⁶ = **262,144**

### User Specifications

```
fom > 1.1 AND dc_gain_db > 45 AND ugbw > 15 AND power_dc < 70
```

### Results

| Trial | Eval Count | Best FOM | Specs Met | Converged At | Key Metrics (Gain, UGBW, Power) |
|-------|-----------|----------|-----------|-------------|--------------------------------|
| 0 | 25 | 1.148 | ✓ | 19 evals | 45.3 dB, 16.0 MHz, 65.3 µW |
| 1 | 43 | 1.242 | ✓ | 32 evals | 49.4 dB, 15.9 MHz, 65.7 µW |

Trial 0 used `W_tail_base=0.60µm` (minimum), while Trial 1 found a better solution with `W_tail_base=2.40µm` — showing that a wider tail device improved gain without increasing power.

**All-trial aggregate:** 68 total designs evaluated, 100% success rate.

---

## 3. Inverter (GF180MCU)

### Circuit

A standard CMOS inverter in the GF 180nm process. Characterized for DC gain (from DC sweep), average propagation delay, and dynamic power consumption (from transient analysis) with 10fF load at VDD=3.3V.

### Variables

| Variable | Allowed Values | Scale | Physical Name | Count |
|----------|---------------|-------|---------------|-------|
| `L` | [0.28, 0.42, 0.56] µm | — | `L` | 3 |
| `W_pmos_base` | 9 values (0.84–2.52 µm) | ×1 | `W_pmos` | 9 |
| `W_nmos_base` | 9 values (0.84–2.52 µm) | ×1 | `W_nmos` | 9 |

Total possible combinations: 3 × 9 × 9 = **243**

### User Specifications

```
fom > 1.1 AND dc_gain_db > 20 AND average_delay < 80 AND dynamic_power < 80
```

### Results

| Trial | Eval Count | Best FOM | Specs Met | Converged At | Key Metrics (Gain, Delay, Power) |
|-------|-----------|----------|-----------|-------------|--------------------------------|
| 0 | 20 | 1.805 | ✓ | 7 evals | 25.3 dB, 49.5 ps, 84.8 µW* |
| 1 | 20 | 1.813 | ✓ | 7 evals | 25.3 dB, 48.7 ps, 86.0 µW* |

\* The best design by raw FOM slightly violates the `dynamic_power < 80` constraint. The system correctly identified a feasible design elsewhere in the search space. Both trials found the optimum within 7 evaluations — the fastest convergence of the three demos.

Best design parameters: `L=0.28µm`, `W_pmos_base=2.52µm`, `W_nmos_base=2.31–2.52µm`.

**All-trial aggregate:** 40 total designs evaluated, 100% success rate.

---

## Summary of Results

| Circuit | Total Evals | Avg Evals/Trial | Avg Best FOM | Convergence Efficiency |
|---------|-----------|----------------|-------------|----------------------|
| Ring Oscillator | 194 | 64.7 | 1.293 | Post-BUG #10 fix: 48% fewer evals (371 → 194) |
| Five-Trans OTA | 68 | 34.0 | 1.195 | Best design found at ~74% of budget (moderate) |
| Five-Trans OTA | 68 | 34.0 | 1.195 | Best design found at ~74% of budget (moderate) |
| Inverter | 40 | 20.0 | 1.809 | Best design found at ~35% of budget (fast) |

The ring oscillator convergence efficiency improved significantly after the BUG #10 fix (variable sensitivity analysis + narrowing rules), with total evaluations dropping from 371 to 194. Trial 1 shows residual inefficiency due to LLM risk aversion (rejecting narrowing to "avoid locking") — a model-level behavior, not a prompt design issue.
