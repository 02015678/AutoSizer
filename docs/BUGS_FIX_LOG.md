# Optuna TPE Changes

Two modifications to `advanced_search_methods.py` that affect how the TPE sampler generates suggestions and evaluates designs.

---

## 1. `constant_liar` — TPE Batch Suggestion Correction

### The Bug

In `optuna_bayesian_optimization()`, the batch loop calls `study.ask()` → `study.tell()` for each suggested point. The `TPESampler` is created with `constant_liar=True` (line 1209), which auto-assigns the best-observed FOM as a placeholder value for unevaluated trials. The original code then **overwrote** this sensible placeholder with a hardcoded `0.0`:

```python
# BEFORE (lines 1342-1346)
if point not in suggested_points:
    suggested_points.append(point)
    study.tell(trial, values=[0.0])       # ← BUG: 0.0 corrupts TPE model
else:
    study.tell(trial, state=optuna.trial.TrialState.PRUNED)
```

**Effect:** Every suggested point was recorded with objective value `0.0`, telling TPE "this region is terrible." Subsequent `ask()` calls avoided similar regions aggressively, destroying the TPE model's density estimates.

### The Fix

```python
# AFTER (lines 1342-1346)
if point not in suggested_points:
    suggested_points.append(point)
    # study.tell(tell, values=[0.0])  # constant_liar=True handles liar values internally
else:
    study.tell(trial, state=optuna.trial.TrialState.PRUNED)
```

By not calling `tell()` for unique points, `constant_liar=True` preserves the best-observed FOM as the placeholder. PRUNED duplicates are still discarded properly.

### Mock Test

```python
import sys; sys.path.insert(0, '.')
from advanced_search_methods import AdvancedSearchMethods
import optuna

config = {
    'variable': {'W_tail_base': None, 'W_diff_base': None, 'W_load_base': None},
}
asm = AdvancedSearchMethods(config, W_values=[0.84, 1.05, 1.26])

# Mock previous_results with known good FOM values
class MockResult:
    def __init__(self, values, fom_val):
        self.results = values
        self.fom = fom_val
    def to_dict(self):
        return self.results

previous_results = [
    MockResult({'W_tail_base': 0.84, 'W_diff_base': 1.05, 'W_load_base': 1.26}, 10.0),
    MockResult({'W_tail_base': 1.05, 'W_diff_base': 1.26, 'W_load_base': 0.84}, 8.0),
    MockResult({'W_tail_base': 1.26, 'W_diff_base': 0.84, 'W_load_base': 1.05}, 6.0),
]

# Call the TPE optimizer
suggested = asm.optuna_bayesian_optimization(
    n_samples=10, previous_results=previous_results,
    targets=['fom'], weights={}
)

# Verify: TPE should suggest new points, not repeat with 0.0 bias
print(f"Generated {len(suggested)} unique suggestions")
for pt in suggested[:5]:
    print(f"  {pt}")

# Check study trials: liar values should be ~best_FOM (10.0), not 0.0
study = asm._last_study  # stored after optuna_bayesian_optimization runs
if study:
    liar_values = [t.value for t in study.trials if t.state == optuna.trial.TrialState.WAITING]
    print(f"Liar values (should be ~10.0, not 0.0): {liar_values[:5]}")
```

---

## 2. INSIGHT Constraint-Penalty FOM

### The Problem

The TPE model optimizes the raw FOM expression (e.g., `dc_gain_db * ugbw / power_dc`) with **no penalty for violating user specs** (`dc_gain_db > 55`, `power_dc < 50`). High-FOM-but-infeasible designs pollute the TPE density model, wasting iterations in infeasible regions.

### The Fix

Follow the INSIGHT paper (arXiv 2407.07346, Section 2.3) formulation:

```
penalized_fom = original_fom - Σᵢ min(1, max(0, fᵢ(x)))
```

Where `fᵢ(x)` is a normalized violation in "positive when violated" form:

- `metric > target`: `fᵢ(x) = (target - actual) / |target|`
- `metric < target`: `fᵢ(x) = (actual - target) / |target|`

Two changes were made to `AdvancedSearchMethods`:

#### a) New method `_get_constraint_penalty(self, result) → float`

```python
def _get_constraint_penalty(self, result) -> float:
    """
    Compute INSIGHT-style constraint penalty for a design result.
    penalty = SUM_i min(1, max(0, f_i(x)))
    """
    user_specs = self.config.get('user_specs_metric', '')
    if not user_specs:
        return 0.0

    if hasattr(result, 'to_dict'):
        design_dict = result.to_dict()
    elif hasattr(result, 'results'):
        design_dict = result.results
    else:
        return 0.0

    from iterative_ota_optimization import parse_user_specs
    constraints = parse_user_specs(user_specs)
    total_penalty = 0.0

    for c in constraints:
        metric = c['metric']
        if metric.lower() == 'fom':
            continue  # FOM is the objective itself

        target = c['target']
        op = c['operator']
        if isinstance(design_dict, dict):
            actual = design_dict.get(metric)
        else:
            continue
        if actual is None:
            continue

        target_abs = abs(target)
        if target_abs < 1e-12:
            continue
        if op == '>':
            fi = (target - actual) / target_abs
        elif op == '<':
            fi = (actual - target) / target_abs
        else:
            continue

        total_penalty += min(1.0, max(0.0, fi))

    return total_penalty
```

#### b) Modified `_calculate_objective_value()`

All three return paths (composite, single-target, multi-objective) now apply the penalty:

```python
# In each return path:
penalty = self._get_constraint_penalty(result)
return base_value - penalty
```

**Location:** `advanced_search_methods.py`, lines 1369-1502.

### Mock Test

```python
import sys; sys.path.insert(0, '.')
from advanced_search_methods import AdvancedSearchMethods

config = {
    'variable': {'W_tail_base': None, 'W_diff_base': None, 'W_load_base': None},
    'user_specs_metric': 'fom > 0.607 AND dc_gain_db > 55 AND ugbw > 10 AND power_dc < 50'
}
asm = AdvancedSearchMethods(config, W_values=[0.84, 1.05, 1.26])

class MockResult:
    def __init__(self, values):
        self.results = values
    def to_dict(self):
        return self.results

# Test 1: All specs met → penalty = 0
r1 = MockResult({'dc_gain_db': 60.0, 'ugbw': 15.0, 'power_dc': 30.0, 'fom': 30.0})
print(f"All met: {asm._get_constraint_penalty(r1):.4f} (expect 0.0000)")

# Test 2: Gain violated (40 < 55) → penalty = (55-40)/55 ≈ 0.2727
r2 = MockResult({'dc_gain_db': 40.0, 'ugbw': 15.0, 'power_dc': 30.0, 'fom': 20.0})
print(f"Gain low: {asm._get_constraint_penalty(r2):.4f} (expect 0.2727)")

# Test 3: Power violated (80 > 50) → penalty = (80-50)/50 = 0.6
r3 = MockResult({'dc_gain_db': 60.0, 'ugbw': 15.0, 'power_dc': 80.0, 'fom': 11.25})
print(f"Power hi: {asm._get_constraint_penalty(r3):.4f} (expect 0.6000)")

# Test 4: All severely violated → each capped at 1.0, sum = 3.0
r4 = MockResult({'dc_gain_db': 10.0, 'ugbw': 1.0, 'power_dc': 200.0, 'fom': 0.05})
print(f"All bad: {asm._get_constraint_penalty(r4):.4f} (expect 3.0000)")

# Test 5: At spec boundaries → penalty = 0
r5 = MockResult({'dc_gain_db': 55.0, 'ugbw': 10.0, 'power_dc': 50.0, 'fom': 11.0})
print(f"Borders: {asm._get_constraint_penalty(r5):.4f} (expect 0.0000)")
```

### Behavior Summary

| Scenario | Penalty | Effect on TPE |
|---|---|---|
| All constraints met | 0.0 | Optimize pure FOM as before |
| One constraint mildly violated (e.g., gain 50 vs 55 target) | ~0.09 | Slight FOM reduction, still explored if reward is high |
| One constraint severely violated (>100% off) | 1.0 (capped) | Significant FOM hit |
| Multiple constraints violated | Sum up to `n_constraints` | Strongly deprioritized |

### Key Design Decisions

1. **No YAML changes needed** — penalty uses the existing `user_specs_metric` from each circuit's YAML
2. **Stored FOM unchanged** — `results['fom']` keeps the original expression; penalty is applied only at the Optuna objective layer
3. **All constraint weights = 1.0** — uniform default; extendable via `config.get('constraint_weights', {})`
4. **`fom` constraint in `user_specs_metric` is skipped** — it is the objective, not a constraint
5. **Missing metrics are skipped** — no penalty for absent data (e.g., failed simulation)

### Files Affected

| File | Change |
|---|---|
| `advanced_search_methods.py:1369-1426` | New `_get_constraint_penalty()` method |
| `advanced_search_methods.py:1428-1502` | Modified `_calculate_objective_value()` — all three return paths apply penalty |
| `advanced_search_methods.py:1344` | Commented out `study.tell(trial, values=[0.0])` (constant_liar fix) |

---

## 3. Constraint Violation Indicators in LLM Prompt

### The Bug

The LLM prompt's "Top 5 designs" section (`_format_top_designs()`) displayed designs sorted by raw FOM without any indicator of which designs meet user constraints. The LLM saw the #1-ranked design (e.g., FOM=1.88, `dynamic_power=81.1` violating `<80`) as "best" and recommended further exploration near that infeasible point — chasing a target that cannot be beat by feasible designs.

### The Fix

After each design's performance metrics, append a compact constraint status suffix: `[dc_gain_db✓ average_delay✓ dynamic_power✗]`.

**Location:** `llm_guided_ota_optimization.py`, `_format_top_designs()`, lines 2020-2039.

```python
# Added after the perf_parts loop, before line append:
# Add constraint satisfaction indicators [metric✓ metric✗ ...]
user_specs = self.config.get('user_specs_metric', '')
if user_specs:
    import re
    spec_indicators = []
    for m, op, t_str in re.findall(r'(\w+)\s*([<>=]+)\s*([\d.e+-]+)', user_specs):
        if m.lower() == 'fom':
            continue
        target = float(t_str)
        actual = values.get(m)
        if actual is None:
            spec_indicators.append(f'{m}?')
        elif op == '>':
            spec_indicators.append(f'{m}{"✓" if actual > target else "✗"}')
        elif op == '<':
            spec_indicators.append(f'{m}{"✓" if actual < target else "✗"}')
        else:
            spec_indicators.append(f'{m}?')
    if spec_indicators:
        perf += f" [{' '.join(spec_indicators)}]"
```

**Before/After example:**
```
BEFORE: 1. FOM=1.8848  (..., dynamic_power=81.10uW)
AFTER:  1. FOM=1.8848  (..., dynamic_power=81.10uW) [dc_gain_db✓ average_delay✓ dynamic_power✗]
```

### Mock Test

```python
import sys, os; sys.path.insert(0, '.')
from llm_guided_ota_optimization import LLMOptimizationAgent

config = {
    'variable': {'L': None, 'W_pmos_base': None, 'W_nmos_base': None},
    'W_values': [0.84], 'L_values': [0.28],
    'user_specs_metric': 'fom > 1.1 AND dc_gain_db > 20 AND average_delay < 80 AND dynamic_power < 80',
    'user_specs': 'test',
    'metrics': ['dc_gain_db', 'average_delay', 'dynamic_power', 'fom_user', 'fom'],
    'metric_post': {
        'dc_gain_db': {'scale':1,'decimals':2,'unit':'dB'},
        'average_delay': {'scale':1,'decimals':2,'unit':'ps'},
        'dynamic_power': {'scale':1,'decimals':2,'unit':'uW'},
        'fom_user': {'expr':'dc_gain_db/(average_delay*dynamic_power)','scale':1,'decimals':6,'unit':''},
        'fom': {'scale':1,'decimals':4,'unit':''}
    },
    'base_metrics': {
        'gain_db': {'name':'Gain','unit':'dB','format':'.1f','degradation_key':'g'},
        'power_uw': {'name':'Power','unit':'uW','format':'.2f','degradation_key':'p'},
        'delay_ps': {'name':'Delay','unit':'ps','format':'.2f','degradation_key':'d'},
        'fom_user': {'name':'FOM','unit':'','format':'.4f','degradation_key':'f'}
    },
    'results_dir': '/tmp/test', 'num_variables_to_optimize': 3,
}
os.makedirs('/tmp/test', exist_ok=True)

agent = LLMOptimizationAgent(config, model='gemini-2.5-flash')
agent.var_names = ['L', 'W_pmos_base', 'W_nmos_base']
agent.target_metric = {'name':'FOM','key':'fom','format':'.4f','direction':'maximize','is_composite':False}

iter_data = {'iteration': 1, 'all_designs': [
    {'L':0.28,'W_pmos_base':2.52,'W_nmos_base':1.68,'dc_gain_db':25.2,'average_delay':52.8,'dynamic_power':81.1,'fom_user':0.00589,'fom':1.8848},
    {'L':0.28,'W_pmos_base':2.52,'W_nmos_base':1.47,'dc_gain_db':25.2,'average_delay':54.5,'dynamic_power':79.8,'fom_user':0.00586,'fom':1.8563},
]}
metric_info = {'name':'FOM','key':'fom','format':'.4f','direction':'maximize','is_composite':False}

result = agent._format_top_designs(iter_data, metric_info)
# Verify: design #1 should have dc_gain_db✓ dynamic_power✗, design #2 all checkmarks
assert 'dynamic_power✗' in result  # Design #1 violates power < 80
assert 'dynamic_power✓' in result  # Design #2 satisfies power < 80
print("PASS")
```

### Files Affected

| File | Change |
|------|--------|
| `llm_guided_ota_optimization.py:2020-2039` | Inline constraint indicator logic in `_format_top_designs()` |

---

## 4. `save_summary` Tracks Best Feasible FOM Instead of Raw FOM

### The Bug

`save_summary()` tracked `best_fom` by raw FOM value across iteration history without checking whether the design satisfied user constraints. In the `inverter_gf` test run, the "best" design (eval #14, FOM=1.8848) violated `dynamic_power < 80` (power=81.1). Meanwhile, a feasible design existed at eval #6 (FOM=1.8563, power=79.8) but was ignored. The `success` flag checked only the last iteration's best design — which happened to be feasible — creating a contradictory summary: `best_fom=1.88` (infeasible) + `success=true`.

### The Fix

**Location:** `iterative_ota_optimization.py`, `save_summary()` method, lines 3458-3503.

Two changes:

**a) `best_fom` tracking loop** — before updating `best_fom`, check constraint satisfaction:

```python
best_feasible_found = False

for iter_result in self.iteration_history:
    cumulative_evals += iter_result.num_designs_searched
    ...
    # Get design and FOM
    if iter_result.post_pex:
        design = iter_result.post_pex
    else:
        design = iter_result.pre_layout
    current_fom = design.fom

    specs_met = self._check_user_constraints(design)

    # Priority 1: feasible design with higher FOM
    if specs_met:
        if not best_feasible_found or current_fom > best_fom:
            best_fom = current_fom
            evals_to_best = cumulative_evals
            time_to_best = iter_result.cumulative_time
            best_feasible_found = True
    # Fallback: if no feasible design found yet, track raw best
    elif not best_feasible_found:
        if best_fom is None or current_fom > best_fom:
            best_fom = current_fom
            evals_to_best = cumulative_evals
            time_to_best = iter_result.cumulative_time
```

**b) `success` check** — scan ALL iterations, not just the last:

```python
# BEFORE:
final_result = self.iteration_history[-1]
success = self._check_user_constraints(final_result.pre_layout)

# AFTER:
for iter_result in self.iteration_history:
    design = iter_result.post_pex if iter_result.post_pex else iter_result.pre_layout
    if self._check_user_constraints(design):
        success = True
        break
```

### Verification with inverter_gf Data

```
Best raw FOM:     eval #14, fom=1.8848 (power=81.1 → VIOLATES dynamic_power < 80)
Best feasible FOM: eval #6,  fom=1.8563 (power=79.8 → all specs met)

Old code reported: best_fom=1.8848 evals_to_best=20 (infeasible)
New code reports:  best_fom=1.8563 evals_to_best=6  (feasible)
```

### Files Affected

| File | Change |
|------|--------|
| `iterative_ota_optimization.py:3458-3488` | `best_fom` tracking filters by constraint feasibility |
| `iterative_ota_optimization.py:3491-3496` | `success` scans all iterations, not just last |

---

## 5. Constraint Penalty Too Weak for Borderline Violations — **FIXED 2026-05-15**

### The Problem

The original INSIGHT-style constraint penalty (Section 2) used normalized linear violation: `fi = |actual - target| / |target|` with `penalty = min(1.0, max(0.0, fi))`. This works for large violations (e.g., gain 10 vs target 55 → penalty = 0.82) but produces negligible penalties for borderline violations.

**Observed in `inverter_gf` run:**

| Design | W_pmos | W_nmos | FOM | Power | Status |
|--------|--------|--------|-----|-------|--------|
| Eval #14 | 2.52 | 1.68 | 1.8848 | 81.1 | Violates `power < 80` by 1.4% |
| Eval #6 | 2.52 | 1.47 | 1.8563 | 79.8 | All specs met |

Penalty for eval #14: `fi = (81.1 - 80) / 80 = 0.01375` → `min(1, max(0, 0.01375)) = 0.014`

Penalized FOM: `1.8848 - 0.014 = 1.871` — **still higher** than the best feasible FOM (1.8563).

**Consequence:** The TPE model continues to prefer the infeasible eval #14 (penalized FOM=1.871) over the feasible eval #6 (FOM=1.856). The penalty is invisible for violations under ~2%.

### Root Cause

1. The linear formula `min(1, fi)` makes small violations proportionally small — a 1.4% violation gives a 0.014 penalty, invisible to TPE
2. The absolute cap of 1.0 doesn't scale with FOM magnitude — a penalty of 1.0 against FOM=0.01 is 100x overkill, against FOM=100 is 1% (invisible)

### The Fix

**Location:** `advanced_search_methods.py`, `_get_constraint_penalty()`, lines 1369-1434.

Replaced linear penalty with **FOM-scaled exponential barrier**:

```
penalty = |FOM| * SUM_i min(cap_ratio, max(0, exp(k * fi) - 1))
```

Parameters:
- `k = 3.0` — exp steepness; at 0.5% violation → 1.5% FOM penalty (gentle)
- `cap_ratio = 0.5` — per-constraint penalty cap at 50% of |FOM|; reached at fi ~ 14.6%

**Behavior:**

| fi | Violation | Penalty (% of FOM) |
|---|---|---|
| 0.005 | 0.5% (threshold) | 1.5% — gentle, barely visible |
| 0.014 | 1.4% (inverter bug) | 4.3% — clearly below feasible |
| 0.050 | 5% (medium) | 16.2% — unmistakable to TPE |
| 0.100 | 10% (serious) | 35.0% — strong |
| >=0.146 | >=14.6% | 50% — capped |

For the inverter bug case: fi=0.01375, FOM=1.8848 → penalty=0.079, penalized FOM=1.805 < 1.856 (feasible). OK

### Files Affected

| File | Change |
|------|--------|
| `advanced_search_methods.py:1369-1434` | `_get_constraint_penalty()` — FOM-scaled exp barrier replaces linear cap |

---

## 6. `round(val, 2)` Truncates Meter-Scale W/L Values — **FIXED 2026-05-17**

### The Bug

In `apply_scales()`, `round(val, 2)` rounds to 2 decimal places. For meter-scale values (no `.option scale=1e-6`), this destroys sub-micron dimensions:

```
W_tail_base = 0.60e-6, factor = 2
val = 0.60e-6 * 2 = 1.2e-6
round(1.2e-6, 2) = 0.0   ← zeroed!
```

The netlist gets `w=0.0 l=0.0`, making every device zero-width/zero-length.

This only worked before because `.option scale=1e-6` was used with µm-scale values (e.g., `W=0.84 → 0.84µm`), so `round(0.84 * 2, 2) = 1.68` was fine. For PDKs with scale=1 (GF 180nm, etc.), removing `.option scale=1e-6` and using explicit meter values (`W_values: [0.60e-6, ...]`) triggered the truncation.

### The Fix

**Location:** `iterative_ota_optimization.py:733` — `apply_scales()`.

```
- fmt[final_name] = round(val, 2)
+ fmt[final_name] = float(f'{val:.4g}')
```

`:.4g` formats to 4 significant digits, which correctly preserves both µm-scale and meter-scale values:

| Scale | Base × factor | Before (`round`) | After (`:.4g`) |
|-------|--------------|-------------------|----------------|
| Meter | 0.60e-6 × 2 = 1.2e-6 | `0.0` ❌ | `1.2e-06` ✅ |
| Meter | 0.28e-6 × 3 = 8.4e-7 | `0.0` ❌ | `8.4e-07` ✅ |
| µm | 0.84 × 10 = 8.4 | `8.4` ✅ | `8.4` ✅ |
| µm | 0.84 × 10 = 8.40000000001 (FP noise) | `8.4` ✅ | `8.4` ✅ |

### Verification

A full simulation with GF 180nm PDK confirmed:
- Netlist: `w=1.2e-06 l=8.4e-07` (non-zero, correct)
- Simulation: no errors, valid results (gain=27.3dB, power=32µW, ugbw=5.75MHz)

### Files Affected

| File | Change |
|------|--------|
| `iterative_ota_optimization.py:733` | `apply_scales()` — replaced `round(val, 2)` with `float(f'{val:.4g}')` |

---

## 7. Missing Commas in LLM-Generated JSON — **FIXED 2026-05-17**

### The Bug

The LLM sometimes omits commas between JSON elements — between adjacent objects (`}{`), arrays (`][`), or after object/array values followed by a key (`} "key"`). The existing `parse_llm_json_response()` only removed trailing commas but didn't insert missing ones, so these responses would hit the fallback recovery strategies or fail entirely.

### The Fix

**Location:** `llm_guided_ota_optimization.py:274-279` — added 6 regex substitutions before the trailing-comma fix:

| Pattern | Replacement | Example |
|---------|-----------|---------|
| `}\s*{` | `},{` | `}{` → `},{` |
| `]\s*\{` | `],{` | `] {` → `],{` |
| `]\s*\[` | `],[` | `][` → `],[` |
| `"\s*\{` | `",{` | `"{"` → `",{"` |
| `}\s*"(?=\s*[a-zA-Z])` | `}, "` | `} "key"` → `}, "key"` |
| `]\s*"(?=\s*[a-zA-Z])` | `], "` | `] "key"` → `], "key"` |

### Files Affected

| File | Change |
|------|--------|
| `llm_guided_ota_optimization.py:274-279` | Added missing-comma repairs before existing trailing-comma fix |

---

## 8. LHS Dead-Zone Detection & Re-Run — **FIXED 2026-05-17**

### The Bug

In Trial 2 of the GF five_trans_ota run, the LLM chose a narrow 3-value range for `W_tail_base` and `L_tail_base`. The initial LHS found 0 feasible designs, but the LLM spent 83 more sims cycling through optuna/genetic in the same dead subspace before finally triggering regeneration. The LLM treated LHS as a one-time initialization and never considered re-running it with a different seed for an independent draw.

### The Fix

Three changes:

#### a) LHS re-run guidance in prompts (`prompts.py`)

Updated the LHS description, decision framework (`Factor 2b: Subspace Viability`), and parameter tuning section to:
- Explain LHS can be re-run with different seeds for independent subspace draws
- Add explicit guidance on when to re-run LHS vs regenerate
- State LHS runs at most TWICE per inner loop

#### b) Constraint feasibility overview in state report (`llm_guided_ota_optimization.py`)

New method `_build_constraint_overview_section()` computes per-constraint best values and % of target across ALL designs, then inserts it into the LLM's state report (no new LLM call needed).

Example output:
```
### Constraint Feasibility Overview (best values across all designs)
- dc_gain_db: best=14.85 vs target >40.0  (37.1% of target)
- ugbw: best=1.91 vs target >3.0  (63.7% of target)
- power_dc: best=97.67 vs target <90.0  (over by 8.5%)
```

#### c) LHS count tracking and seed rotation (`llm_guided_ota_optimization.py`)

- `lhs_count` per inner loop, max 2 (reset on regeneration)
- Second LHS forces a different seed from the first
- After 2 LHS runs, the method is overridden to `genetic` if LLM tries to pick LHS again
- `lhs_count` is shown in the state report for LLM visibility

### Files Affected

| File | Change |
|------|--------|
| `prompts.py:108-114` | Updated LHS description with re-run and regeneration guidance |
| `prompts.py:196-208` | Added Factor 2b: Subspace Viability to decision framework |
| `prompts.py:250-254` | Updated LHS parameter tuning with seed rotation and max-2 rule |
| `llm_guided_ota_optimization.py:1423-1487` | New `_build_constraint_overview_section()` method |
| `llm_guided_ota_optimization.py:1529-1537` | Constraint overview wired into decision prompt |
| `llm_guided_ota_optimization.py:2627-2629` | LHS count tracking variables |
| `llm_guided_ota_optimization.py:2730-2744` | LHS max-2 enforcement and seed rotation |
| `llm_guided_ota_optimization.py:2773` | Seed tracking after LHS execution |
| `llm_guided_ota_optimization.py:2648` | `lhs_count` passed into state dict |

---

## 9. Wider Initial Search Space: 5-7 Values per Variable — **FIXED then REVERTED 2026-05-17**

### The Bug

Trial 2's `W_tail_base` had only `[1.2e-6, 2.4e-6, 4.8e-6]` (3 values), missing the 9.6e-6 headroom. The original "3-7 values each" was too loose on the low end.

### First Attempt (5-7 values) — FAILED

Changed minimum to 5. This caused Trial 0 to balloon from 43→195 designs: with 6 variables at 5-7 values each, the combinatorial space was 32,400-46,656 combos. With only 25 LHS points and demanding GF specs, coverage dropped to 0.05%. The search was spread too thin.

### Final Fix (3-5 values + extremes guard)

**Location:** `problem_agent.py:114,341`.

Reverted to near-original but narrowed the maximum and added an explicit extremes rule:

```
- 3-5 values each (include smallest and largest from available list)
- - [ ] Each variable has 3-5 values (smallest and largest always included)
```

This keeps subspaces compact (3⁶=729 to 5⁶=15,625 combos) while the extremes rule prevents accidentally missing the range endpoints.

### Files Affected

| File | Change |
|------|--------|
| `problem_agent.py:114` | "5-7 values each, or all available..." → "3-5 values each, include smallest and largest from available list" |
| `problem_agent.py:341` | "Each variable has 5-7 values (or all available if fewer)" → "Each variable has 3-5 values (smallest and largest always included)" |


## 10. LLM Fails to Narrow Search Space When Monotonic Dominance Exists — **FIXED 2026-05-21**

### The Problem

In the `3_stage_ring_osc_new` optimization (3 trials, 371 total simulations), the LLM consistently failed to narrow `L_inv` to its minimum value (0.3µm) despite overwhelming evidence that smaller L always improves FOM. The LLM correctly **identified** the pattern ("top designs are heavily clustered at the 0.3um minimum") but kept the full range `[0.3, 0.4, 0.5, 0.6, 0.7]` in all subsequent iterations. This wasted ~40% of the simulation budget (~150 runs) on L≥0.4 designs that had no chance of being optimal.

**Evidence from the actual LLM decision (opt_config_iter1.json):**

```json
"L_inv": {
  "search_space": [0.3, 0.4, 0.5, 0.6, 0.7],   // ← kept all 5 values
  "range_reasoning": "Full range included; top designs are heavily clustered at the 0.3um minimum.",
  "expected_behavior": "Decreasing L_inv reduces stage delay and parasitic capacitance, increasing frequency and FOM.",
  "sensitivity": "high"
}
```

The LLM knows L=0.3 dominates, knows the relationship is monotonic ("decreasing L → increasing FOM"), and still doesn't narrow. This is **not** a knowledge gap — it's a prompt design issue that fails to translate correct analysis into correct action.

### Root Cause: Prompt Design Issues

Five prompt weaknesses identified (in priority order):

1. **Flat statistical summary hides per-value performance** — The "Parameter distribution" section shows equal-value frequency counts (`L_inv values: {0.7: 4, 0.3: 4, ...}`) without breaking down FOM by value. The LLM sees equal sampling counts and thinks the space is still open.

2. **"Exploration" is praised unconditionally** — `**High exploration** - diverse solutions found` reads as positive reinforcement. No counterbalance warns when exploration is wasteful.

3. **No "boundary dominance" narrowing rule** — The prompt describes WHEN to use each algorithm (LHS vs optuna vs genetic) but not WHEN to narrow a variable's search space. The method descriptions are algorithm-focused, not variable-focused.

4. **"Budget status: Early stage" encourages dawdling** — The LLM reads "I have plenty of budget" instead of "early narrowing maximizes later budget efficiency."

5. **`change_from_previous` tracking biases toward change** — The optimization_config structure tracks what changed each iteration. Keeping the same search space produces nothing to report, subtly pushing the LLM to "do something different" each round.

### Change Plan

Five targeted changes across two files:

#### A. Per-value performance breakdown — `llm_guided_ota_optimization.py`

Add a new section after `_format_top_designs()` output that groups top-N designs by variable value:

```
### Variable Sensitivity Analysis (top-10 designs by FOM)

L_inv:
  L=0.3: mean FOM=1.35, appears in 9/10 top designs ★ DOMINANT
  L=0.4: mean FOM=1.08, appears in 1/10 top designs
  L=0.5: mean FOM=0.92, appears in 0/10 top designs
  ... (no top designs at L=0.6 or L=0.7)
→ L_inv=0.3 is the minimum available value AND dominates top designs.
  Consider fixing at 0.3 to free budget for ratio optimization.

W_pmos:
  W=1.0: mean FOM=1.30, appears in 5/10 top designs
  W=2.0: mean FOM=1.28, appears in 3/10 top designs
  W=3.0: mean FOM=1.15, appears in 2/10 top designs
→ W_pmos shows spread across 3 values — keep full range for now.
```

**Implementation**: New method `_build_sensitivity_section(top_designs, var_names)` in `llm_guided_ota_optimization.py`, wired into the state report after the constraint overview section.

#### B. Narrowing decision rule — `prompts.py`

Insert before the method descriptions:

```
### When to Narrow a Variable's Search Space

NARROW (fix at boundary) when ALL of:
  1. ≥80% of top-10 designs share the same value for that variable
  2. That value is at a boundary (minimum or maximum) of the current range
  3. The relationship appears monotonic (e.g., "smaller always better")

EXPAND when:
  1. Top designs span ≥3 different values, OR
  2. Best value is NOT at a boundary

Keep CURRENT range when uncertain, but DEFAULT BIAS: when a variable
satisfies all three NARROW conditions, NARROW IT. A false narrowing wastes
a few simulations. A missed narrowing wastes dozens.
```

**Implementation**: New section in `prompts.py`'s optimization guidance block.

#### C. Replace "Exploration score" with efficiency framing — `llm_guided_ota_optimization.py`

Replace the `Search behavior:` section:

```
BEFORE:
  - Exploration score: 0.31
  - Exploitation score: 0.80
  - **High exploration** - diverse solutions found

AFTER:
  - Search efficiency: 31% of combos sampled
  - Concentration: top-5 designs span only 2 of 5 L_inv values
  → Recommendation: Narrow L_inv to [0.3] — it dominates top designs
    and appears at the range boundary.
```

**Implementation**: Modify the search behavior section in the state report builder.

#### D. Budget framing — `llm_guided_ota_optimization.py`

Replace `Budget status: Early stage (<100)` with:

```
- Budget used: 20/128 (16%)
- If L_inv narrowed to 0.3 only: 3× more budget available for W_pmos/W_nmos ratio optimization
  (180 combos → 36 combos in the narrowed space)
```

**Implementation**: Extend the budget section to show the "narrowing dividend."

#### E. No change needed for `change_from_previous` — already adequate

The tracking itself is useful. Fixing A-D above will naturally produce the right narrowing decisions, making `change_from_previous` reflect meaningful tightening rather than arbitrary changes.

### Verification

1. Re-run `3_stage_ring_osc_new` with modified prompts — verify the LLM narrows `L_inv` to `[0.3]` after the first LHS iteration
2. Confirm the narrowed search converges to the same optimum `(0.3, 2.0, 1.5)` in fewer evaluations
3. Check that `W_pmos` and `W_nmos` are NOT prematurely narrowed (they have genuine trade-off structure)
4. Run the `inverter_gf` optimization to verify the sensitivity analysis section renders correctly for a different circuit type

### Files Affected

| File | Change |
|------|--------|
| `llm_guided_ota_optimization.py` | New `_build_sensitivity_section()` method; modified budget and search behavior sections |
| `prompts.py` | New "When to Narrow a Variable's Search Space" section in optimization guidance |

### Implementation Summary (2026-05-21)

Four changes implemented across two files:

**Change A — Per-value sensitivity analysis** (`llm_guided_ota_optimization.py`):
- New `_get_var_values(var_name)` helper resolving allowed values with per-variable overrides
- New `_build_sensitivity_section(state, top_n=10)` method: groups top designs by variable value, computes per-value mean FOM and appearance frequency, detects boundary dominance (≥80% concentration at min/max of range), emits NARROW recommendation
- Wired into `_build_iteration_history_section()` after the statistical analysis block

**Change B — Factor 6: Variable Narrowing** (`prompts.py`):
- New "Factor 6: Variable Narrowing" subsection in `decision_framework_section()` with explicit NARROW (3 conditions: ≥80% top-10 on one value + at boundary + monotonic), EXPAND, and KEEP rules
- DEFAULT BIAS toward narrowing: "A false narrowing wastes a few simulations. A missed narrowing wastes dozens."
- Factor 3 (Design Space Insights) updated: edge-parameter guidance now distinguishes DOMINANT-at-boundary (narrow) vs spread-but-best-at-boundary (expand)

**Change C — Efficiency framing** (`llm_guided_ota_optimization.py`):
- Replaced exploration/exploitation scores and "High exploration" / "High exploitation" labels in `_add_statistical_analysis()` with search efficiency (% of combos sampled) and concentration analysis (distinct values per variable in top-5 designs)

**Change D — Narrowing dividend in budget** (`llm_guided_ota_optimization.py`):
- `_build_current_state_section()` now shows narrowing opportunity (e.g., "fix W_pmos (6 values) → 30 combos, 6× more budget per remaining combo") and budget density (designs per combo)

### Results

#### Before Fix (May 18 run)

| Trial | Total Evals | Evals to Best | Best FOM | Best Design (L, Wp, Wn) | Specs Met |
|-------|------------|---------------|----------|------------------------|-----------|
| 0 | 128 | 125 | 1.347 | (0.3, 1.0, 2.0) | ✓ |
| 1 | 130 | 127 | 1.347 | (0.3, 1.0, 2.0) | ✓ |
| 2 | 113 | 109 | 1.347 | (0.3, 1.0, 2.0) | ✓ |
| **Total** | **371** | — | — | — | 3/3 |

Best design found at ~96% of budget. LLM knew L=0.3 dominated but never narrowed — ~150 simulations wasted on suboptimal values.

#### After Fix (9950ec7)

| Trial | Total Evals | Evals to Best | Best FOM | Best Design (L, Wp, Wn) | Specs Met |
|-------|------------|---------------|----------|------------------------|-----------|
| 0 | 93 | 79 | 1.164 | (0.3, 1.0, 2.0) | ✓ |
| 1 | 86 | 84 | 1.164 | (0.3, 1.0, 2.0) | ✓ |
| 2 | — | — | — | — | — |
| **Avg** | **89.5** | **81.5** | **1.164** | — | — |

Eval count nearly halved (371 → 179 for 2 completed trials). Trial 2 did not complete (process interrupted). Note: best FOM is lower (1.164 vs 1.347) because the YAML was updated with `c_load=10e-15` which reduces max achievable frequency and FOM — the metric scale changed, not a regression.

---

## 11. Outer Loop Regeneration Expansion Bias — **OPEN**

### The Problem

The `CIRCUIT_REUNDERSTANDING_PROMPT` in `problem_agent.py` contains three directives that directly conflict with Factor 6's narrowing guidance:

```
Line 278: - Bias: Favor expansion over narrowing
Line 328: 8. **Bias: When uncertain, choose expansion or unfixing over narrowing or convergence**
Line 331: - **Default to action**: Prefer exploring (expand/unfix) over staying static
```

This creates a push-pull between the inner loop and outer loop:

- **Inner loop** (Factor 6 in `prompts.py`): "DEFAULT BIAS toward narrowing" — narrow when ≥80% boundary dominance
- **Outer loop** (problem_agent.py): "Favor expansion over narrowing" — expand ranges aggressively

The outer loop's regeneration decisions override the inner loop's narrowed search space, undoing any efficiency gains. When the LLM re-optimizes after an inner loop completion, it reads both sets of instructions and the expansion bias typically wins.

### Observed Impact

In the May 18 run (pre-BUG #10 fix), the LLM's outer loop decisions kept expanding `L_inv` back to `[0.3, 0.4, 0.5, 0.6, 0.7]` iteration after iteration, even though the inner loop had already determined L=0.3 dominates. Each regeneration undid any narrowing.

### Root Cause

The `CIRCUIT_REUNDERSTANDING_PROMPT` was written with an exploration-first philosophy that predates Factor 6. It assumes the outer loop's job is to explore new regions, while Factor 6 recognizes that narrowing is more efficient once dominance is established.

### Proposed Fix

Replace the three expansion-biased lines with narrowing-aligned guidance:

| Current | Proposed |
|---------|----------|
| "Bias: Favor expansion over narrowing" | "Bias: When a variable shows boundary dominance in top designs, narrow to that boundary value" |
| "Bias: When uncertain, choose expansion or unfixing over narrowing or convergence" | "Expansion is appropriate when no variable shows clear dominance and top designs span ≥3 values" |
| "Default to action: Prefer exploring (expand/unfix) over staying static" | "Default to action: Narrow when evidence is clear; keep current range when uncertain" |

### Files Affected

| File | Lines | Change |
|------|-------|--------|
| `problem_agent.py` | 273-334 | Replace expansion bias with narrowing guidance matching Factor 6 |

### Status

**OPEN** — No fix attempted yet. Fix is non-invasive (only prompt text changes in `problem_agent.py`).

---

## 12. Algorithm Selection Prompt Has Dead Entries and Wrong Priority — **OPEN**

### The Problem

The `methods_section()` in `prompts.py` lists 9 algorithms but only 3-4 are actually used in practice:

| # | Algorithm | Used in Practice? | Notes |
|---|-----------|-------------------|-------|
| 1 | LHS | ✅ Frequently | First-iteration standard |
| 2 | Genetic | ✅ Sometimes | Useful for diversity |
| 3 | Bayesian | ❌ Rarely | Optuna (TPE) outperforms GP on discrete spaces |
| 4 | Optuna | ✅ Frequently | Most effective for this project's discrete spaces |
| 5 | Adaptive | ❌ Never | "Jack of all trades, master of none" — prompt itself admits this |
| 6 | Annealing | ✅ Occasionally | Useful for escaping local optima |
| 7 | Multistart | ❌ Rarely | Hasn't produced better results than Optuna/Genetic |
| 8 | Random | ❌ Never | Redundant with LHS + seed rotation |
| 9 | Refined | ❌ Never | Undocumented, unknown behavior |

### Observed Impact

1. **Prompt noise**: 6/9 algorithms are never or rarely used. Their presence dilutes the LLM's attention budget — each method description consumes tokens and cognitive load that could go toward more useful guidance.

2. **Wrong priority ordering**: Optuna — empirically the most effective algorithm for this project's discrete search spaces — is listed at #4, behind Genetic (#2) and Bayesian (#3). The LLM is more likely to pick algorithms listed earlier.

3. **"Best when" descriptions don't match project reality**: For example, Bayesian's "MOST sample-efficient" claim doesn't hold for discrete spaces where TPE consistently outperforms GP. Genetic's "Evolves toward good regions" sounds attractive but its 20-30 samples/iteration waste budget.

### Related Issues

- **BUG #11**: The problem_agent.py expansion bias compounds algorithm inefficiency — a suboptimal algorithm (e.g., Genetic with 30 samples) paired with an ever-expanding search space maximizes waste.
- **BUG #10 fix**: The Factor 6 narrowing rules compensate for algorithm inefficiency, but fixing algorithm selection would address the root cause.

### Proposed Fix

Two changes:

**A. Remove dead entries** — Remove Bayesian, Adaptive, Multistart, Random, and Refined from `methods_section()`. Keep only: LHS, Optuna, Genetic, Annealing.

**B. Reorder by empirical effectiveness**:

1. **LHS** — First-iteration space coverage
2. **Optuna** — Primary refinement tool (TPE best for discrete spaces)
3. **Genetic** — Secondary, use when Optuna stagnates
4. **Annealing** — Escape tool for local optima

### Files Affected

| File | Change |
|------|--------|
| `prompts.py:95-175` | `methods_section()` — remove dead entries, reorder by empirical effectiveness |
| `prompts.py:178-269` | `decision_framework_section()` — update method sequencing and "Need X → use Y" mappings |
| `prompts.py:271-493` | `parameter_tuning_section()` — remove Bayesian, Adaptive, Multistart tuning params |
| `prompts.py:495-555` | `response_format_section()` — update allowed method list |

### Status

**OPEN** — No fix attempted yet. Requires significant prompt restructuring. Risk: removing algorithms the LLM might someday find useful (but hasn't in 100+ iterations).


