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
