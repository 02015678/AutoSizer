# Optimization Algorithms in AutoSizer

AutoSizer uses a meta-optimization framework where an LLM agent dynamically selects from a pool of optimization algorithms each iteration. All algorithms operate on **discrete search spaces** — per-variable lists of allowed values (e.g., transistor widths from 0.84µm to 2.52µm in SKY130). The LLM decides which algorithm to use, how many samples to draw, and with what hyperparameters, based on the current optimization state.

---

## Architecture: One Objective, One Feedback Path

All algorithms share a single objective function and a single constraint-checking pipeline:

```
algorithm generates candidate points
  → ngspice simulation
  → metric_post evaluation (extract DC gain, UGBW, power, etc.)
  → _calculate_objective_value() applies constraint penalty
  → hybrid sort ranks designs (feasible-first, then by penalized FOM)
  → check_user_specs_met() validates all hard constraints
```

**No algorithm has its own FOM computation or constraint logic.** This ensures uniform behavior — adding a new algorithm requires no per-circuit or per-constraint code.

---

## Algorithm Pool

### 1. LHS — Latin Hypercube Sampling

| Property | Value |
|---|---|
| **Type** | Pure exploration |
| **Best when** | Initial iteration, zero prior data, broad space coverage needed |
| **Sample size** | 15–30 |
| **Key parameters** | `seed` |

**How it works:** Divides each variable's range into `n` equal-probability intervals and samples one point from each interval, ensuring uniform coverage of the multidimensional space. In AutoSizer, LHS is also used as a **fallback** when other algorithms have insufficient historical data (e.g., TPE with <3 prior evaluations).

**Pros:**
- Guarantees uniform space coverage — no blind spots
- Zero dependence on prior results — unbiased first look
- Fast, no model training

**Cons:**
- Completely ignores previous results — no learning
- Wastes samples in clearly poor regions on later iterations
- Quality degrades with very small sample counts

---

### 2. Genetic Algorithm

| Property | Value |
|---|---|
| **Type** | Evolutionary exploration |
| **Best when** | 10–50 prior designs, want diverse good candidates, rugged fitness landscape |
| **Sample size** | 15–30 |
| **Key parameters** | `mutation_rate` (0.1–0.4), `crossover_rate` (0.7–0.9), `tournament_size` (2–4) |

**How it works:** Maintains a population of design points. Each generation: (1) tournament selection picks parents, (2) crossover combines parent genes (variable values), (3) mutation randomly flips values. The population evolves toward higher-fitness regions over generations. Initial population is seeded from top historical designs sorted by penalized FOM.

**Pros:**
- Robust to local optima — population maintains diversity
- Finds multiple good solutions simultaneously
- Naturally handles discrete search spaces

**Cons:**
- Needs many evaluations per generation
- Slower convergence than Bayesian methods
- Hyperparameter-sensitive (mutation/crossover rates)

---

### 3. Bayesian Optimization (Gaussian Process)

| Property | Value |
|---|---|
| **Type** | Intelligent exploitation |
| **Best when** | 25+ prior designs, budget-limited, want sample efficiency |
| **Sample size** | 10–20 |
| **Key parameters** | `acquisition_function` (EI / UCB / LCB / PI), `exploration_weight` (kappa, 1.0–3.0) |

**How it works:** Builds a Gaussian Process (GP) surrogate model over the objective landscape. The acquisition function balances exploration (high uncertainty) vs exploitation (high predicted value). The GP is trained on historical data with the penalized FOM as the target value. scikit-optimize's `gp_minimize` handles the optimization.

**Acquisition functions:**

| Function | Behavior |
|---|---|
| **EI** (Expected Improvement) | Default. Balances probability and magnitude of improvement |
| **UCB** (Upper Confidence Bound) | µ + κ·σ. High κ forces exploration |
| **LCB** (Lower Confidence Bound) | µ − κ·σ. Conservative exploitation |
| **PI** (Probability of Improvement) | Pure probability of beating best |

**Pros:**
- Most sample-efficient method — learns from all data
- Provides uncertainty estimates (useful for exploration/exploitation balance)
- Smooth surrogate handles noisy evaluations

**Cons:**
- GP fits poorly on discrete/categorical spaces (AutoSizer works around this)
- Can get trapped in local optima if exploration was insufficient
- Computational cost scales O(n³) with data size
- Falls back to LHS if <3 valid evaluations

---

### 4. Optuna TPE (Tree-structured Parzen Estimator)

| Property | Value |
|---|---|
| **Type** | Intelligent exploration + exploitation |
| **Best when** | 15+ prior designs, discrete design space, want native categorical support |
| **Sample size** | 12–18 |
| **Key parameters** | `n_ei_candidates` (20–60), `multivariate` (true/false), `constant_liar` (true) |

**How it works:** TPE builds two density models over the parameter space: `l(x)` for "good" designs (top γ-quantile of observed FOM) and `g(x)` for the rest. New points are suggested by maximizing the ratio `l(x)/g(x)` — i.e., points that are likely under the good distribution but unlikely under the bad one. Unlike GP, TPE natively handles categorical/discrete variables.

**Key features in AutoSizer:**
- `multivariate=True`: models parameter correlations (meaningful for transistor sizing where widths interact)
- `constant_liar=True`: assigns best-observed FOM as placeholder for unevaluated suggestions (preserves model quality during batch suggestion)
- Historical designs loaded as `CategoricalDistribution` trials

**Pros:**
- Native discrete/categorical support — no workarounds needed
- Handles correlated variables (multivariate mode)
- More robust to local optima than GP
- Efficient with small-to-medium data

**Cons:**
- Requires 3+ historical evaluations (falls back to LHS otherwise)
- Slightly more complex hyperparameters
- Batch suggestion uses liar values (approximate)

---

### 5. Adaptive Search

| Property | Value |
|---|---|
| **Type** | Balanced exploration + exploitation |
| **Best when** | Unsure whether to explore or exploit, want self-balancing approach |
| **Sample size** | 15–25 |
| **Key parameters** | `explore_weight` (0.3–0.5), `exploit_weight` (0.3–0.5), `random_weight` (0.1–0.3), `radius` (1–3) |

**How it works:** Three strategies in parallel: (1) **exploration** — sample from unexplored regions of the parameter space, (2) **exploitation** — local search around top-ranked designs (by penalized FOM), (3) **random** — purely random points for diversity. The weights control the proportion of samples from each strategy. Regions are scored by a combination of exploration potential (distance from known points) and exploitation score (average penalized FOM in the region).

**Pros:**
- Self-balancing — no need to decide explore vs exploit
- Good default when optimization state is ambiguous
- Finds diverse solutions across the space

**Cons:**
- Jack of all trades, master of none
- Less sample-efficient than pure Bayesian/TPE
- Region scoring adds computational overhead

---

### 6. Simulated Annealing

| Property | Value |
|---|---|
| **Type** | Escape + exploration |
| **Best when** | Stuck in plateau, suspect local optimum, need to escape |
| **Sample size** | 12–20 |
| **Key parameters** | `initial_temperature` (1.0–3.0), `cooling_rate` (0.85–0.99) |

**How it works:** Starts from the best known design and proposes random perturbations. Better designs are always accepted; worse designs are accepted with probability `exp(−ΔF / T)` where T is the current temperature. Temperature decreases over time (T ← T × cooling_rate), gradually reducing the acceptance of worse solutions. This allows the algorithm to escape local optima early when temperature is high, then converge as it cools.

**Fitness evaluation:** Uses `_calculate_objective_value` — same penalized FOM as all other algorithms.

**Pros:**
- Can escape local optima that trap greedy methods
- Theoretically converges to global optimum with proper cooling schedule
- Simple, well-understood algorithm

**Cons:**
- May waste samples exploring bad regions (especially early, high-T phase)
- Sensitive to temperature schedule and cooling rate
- Single-point search — doesn't leverage population diversity

---

### 7. Multi-Start Local Search

| Property | Value |
|---|---|
| **Type** | Diversified exploitation |
| **Best when** | Late stage, verifying convergence, want alternative feasible designs |
| **Sample size** | 12–25 |
| **Key parameters** | `n_starts` (3–8), `radius` (1–3) |

**How it works:** Selects the top-N designs (by penalized FOM) as starting points, then performs local search around each — perturbing one variable at a time within a small radius. This finds multiple local optima, providing the LLM with diverse high-quality candidates for trade-off analysis.

**Pros:**
- Finds multiple local optima — not just the single best
- Provides alternative solutions for designer trade-off analysis
- Good for convergence verification

**Cons:**
- Inefficient if only one dominant optimum exists
- Local search radius must match search space granularity
- Can waste samples if starting points are in the same basin

---

### 8. Refined Search

| Property | Value |
|---|---|
| **Type** | Targeted local exploitation |
| **Best when** | Very late stage, fine-tuning known good region |
| **Sample size** | 10–15 |
| **Key parameters** | `radius` (1–2) |

**How it works:** Local search with small perturbations around the current best design. Similar to multi-start but focused on a single region.

### 9. Random Search

| Property | Value |
|---|---|
| **Type** | Pure randomness |
| **Best when** | Debugging, baseline comparison |
| **Sample size** | Any |

Uniform random sampling from the discrete search space with no learning. Used sparingly — primarily as a baseline or debugging tool.

---

## Algorithm Selection Logic

### Who Decides?

The **LLM agent** selects the algorithm each iteration. It receives a comprehensive state report including:

- All previous designs with metrics and constraint satisfaction indicators `[dc_gain✓ power✗ ...]`
- Statistical analysis: mean, median, std, percentiles of FOM distribution
- Parameter distributions: which values of each variable are being explored
- Convergence analysis: plateau detection, improvement trends
- Boundary clustering: whether top designs cluster at search space edges

### Decision Factors

The LLM applies these heuristics (learned from the prompt descriptions):

| State | Recommended Algorithms | Rationale |
|---|---|---|
| **0 designs** (first iteration) | `lhs` | Unbiased space coverage. No data to learn from. |
| **<10 designs**, early exploration | `lhs`, `genetic` | Need more coverage before model-based methods are reliable. |
| **10–25 designs**, building knowledge | `genetic`, `adaptive`, `optuna` | Enough data to start learning patterns. TPE works at 15+. |
| **25+ designs**, sample-efficient phase | `bayesian`, `optuna` | Surrogate models can exploit accumulated knowledge efficiently. |
| **Plateau detected** (FOM stagnant) | `annealing`, `multistart` | Need to escape local optimum or verify convergence. |
| **Late stage**, verifying convergence | `multistart`, `refined` | Find alternative optima, fine-tune best design. |
| **Budget nearly exhausted** | `bayesian`, `optuna` | Maximum sample efficiency for final push. |
| **High boundary clustering** | `genetic`, `adaptive` | Expand exploration — top designs at search space edges suggest better points outside current range. |

### The LLM's JSON Decision

```json
{
  "action": "search",
  "method": "optuna",
  "n_samples": 15,
  "parameters": {
    "n_ei_candidates": 45,
    "multivariate": true
  },
  "reasoning": "25 designs available with TPE. Top designs cluster at max W_pmos, suggesting further gains near this boundary. Optuna handles discrete space natively and will efficiently explore this region.",
  "confidence": "high",
  "expected_improvement": "5-10% FOM improvement",
  "convergence_assessment": "Not converged. Best design at edge of search range."
}
```

### Stopping Rules

The LLM can also decide to `"stop"` when:
- FOM improvement < 0.1% for 2+ consecutive iterations with diverse methods tried
- 3+ iteration plateau with no new best design
- The LLM determines the search has converged

### Outer Loop: Regeneration

When the inner loop converges without meeting specs, the **outer loop** triggers regeneration: the LLM re-analyzes the search history and decides to expand ranges, unfix variables, or shift optimization focus — then a new inner loop begins with the revised search space. This mimics a human designer's "re-think" when initial sizing strategies don't meet specs.

---

## Flowchart

![Algorithm Selection Flow](../algorithm_selection_flow.png)

> **Placeholder:** Replace `../algorithm_selection_flow.png` with the exported Mermaid diagram from `../algorithm_selection_flow.md`.

---

## Summary

| Algorithm | Needs Prior Data | Sample Efficiency | Escape Local Optima | Discrete-Native |
|---|---|---|---|---|
| LHS | No | Low | N/A | Yes |
| Genetic | Yes (10+) | Medium | Yes | Yes |
| Bayesian (GP) | Yes (25+) | High | No | No (workaround) |
| Optuna (TPE) | Yes (15+) | High | Moderate | **Yes** |
| Adaptive | Yes (10+) | Medium | Moderate | Yes |
| Simulated Annealing | Yes (5+) | Low-Medium | **Yes** | Yes |
| Multi-Start | Yes (15+) | Medium | Yes | Yes |
| Refined | Yes (20+) | Low | No | Yes |
| Random | No | Very Low | N/A | Yes |

**Bottom line:** AutoSizer delegates algorithm selection to an LLM that reads the full optimization state — designs, statistics, plateaus, boundary clusters — and picks the method best suited to the current phase. All algorithms share one penalized objective and one constraint-checking pipeline, so the LLM can freely switch strategies without worrying about per-algorithm FOM inconsistencies.
