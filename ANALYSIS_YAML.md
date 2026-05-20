# YAML Config Structure Analysis

## Variable & Search Space Matching

### The Problem

The YAML defines a single vector for widths and lengths:

```yaml
W_values: [0.84, 1.05, 1.26, 1.47, 1.68, 1.89, 2.10, 2.31, 2.52]
L_values: [0.30, 0.45, 0.90, 1.20, 1.50, 1.80, 2.10, 2.40, 2.70]
```

But there are multiple `W_*` and `L_*` base variables:

```yaml
variable:
  W_tail_base: null
  W_diff_base: null
  W_casc_base: null
  W_load_base: null
  L_tail_base: null
  L_diff_base: null
  L_casc_base: null
  L_load_base: null
```

**How does one `W_values` serve all `W_*` variables?** By **name prefix matching** — if a variable starts with `W_`, it gets `W_values`; if it starts with `L_`, it gets `L_values`. Every `W_*` variable gets the exact same list.

### Code Flow

```
In search_designs() (iterative_ota_optimization.py:2718-2732):

  for var_name in self.var_names:
      # Priority 1: variable-specific override
      if f"{var_name}_values" in self.config:
          var_values[var_name] = self.config[f"{var_name}_values"]
      # Priority 2: generic W_values/L_values
      elif var_name.startswith('W_') and 'W_values' in config:
          var_values[var_name] = self.config['W_values']   # ALL W_* get SAME list
      elif var_name.startswith('L_') and 'L_values' in config:
          var_values[var_name] = self.config['L_values']   # ALL L_* get SAME list
```

The same logic exists in `make_provider()` (line 504-537) for resolving values during netlist rendering.

### Giving Different Variables Different Ranges

Use **per-variable overrides** in the YAML (key format: `{exact_var_name}_values`):

```yaml
W_values: [0.84, 1.05, 1.26, 1.47, 1.68, 1.89, 2.10, 2.31, 2.52]   # fallback

W_tail_base_values:  [0.84, 1.05, 1.26]          # tail: narrow range
W_diff_base_values:  [1.68, 1.89, 2.10, 2.31]    # diff: wide upper
W_casc_base_values:  [1.05, 1.26, 1.47, 1.68]    # casc: mid range
W_load_base_values:  [0.84, 1.05]                 # load: small values
```

These take priority in both `search_designs()` and `make_provider()`.

### LLM-Level Per-Variable Narrowing

Even if all variables share the same `W_values`, the LLM's `optimization_config` assigns **per-variable** search spaces independently. During search space reduction, the LLM returns:

```json
"optimization_configuration": {
  "variables_to_optimize": {
    "W_tail_base": { "search_space": [0.84, 1.05, 1.26], ... },
    "W_diff_base": { "search_space": [1.68, 1.89, 2.10], ... }
  }
}
```

This is applied in `AdvancedSearchMethods.__init__()` (advanced_search_methods.py:56-58) and overrides the generic default.

## `width_scales` / `length_scales` System

### Structure

Both are flat dicts mapping a **scaled name** to `[base_variable, multiplier]`:

```yaml
width_scales:
  W_tail:   [W_tail_base, 2]    # W_tail = W_tail_base × 2
  W_diff:   [W_diff_base, 4]    # W_diff = W_diff_base × 4
  W_casc:   [W_casc_base, 2]    # W_casc = W_casc_base × 2
  W_load:   [W_load_base, 2]    # W_load = W_load_base × 2
```

### Key Properties

1. **One block per type** — exactly one `width_scales` key and one `length_scales` key in the YAML. No support for multiple independent groups like `width_scales_group1`, `width_scales_group2`.

2. **Each entry IS already independent** — every scaled name maps to its own base variable and its own multiplier. There is no grouping constraint. `W_tail` is independent of `W_diff`.

3. **Code processing** (`apply_scales()` in iterative_ota_optimization.py:696-733):
   - For each `final_name: [base_var, factor]`, resolves `base_var` via `resolve_key()` (from trial values or config ranges)
   - Computes `fmt[final_name] = round(fmt[base_var] * factor, 2)`
   - Skips if `final_name` already has a non-None value in `fmt`

4. **Rounding**: scaled values are rounded to 2 decimal places.

### Multiple Groups Example

To group W1/W2 under one variable and W3/W4 under another, both independent:

```yaml
variable:
  W_group1_base: null
  W_group2_base: null

W_group1_base_values: [0.84, 1.05, 1.26]
W_group2_base_values: [1.68, 1.89, 2.10]

width_scales:
  W1: [W_group1_base, 2]    # W1 = W_group1_base × 2
  W2: [W_group1_base, 2]    # W2 = W_group1_base × 2 (shares group1 var)
  W3: [W_group2_base, 4]    # W3 = W_group2_base × 4
  W4: [W_group2_base, 4]    # W4 = W_group2_base × 4 (shares group2 var)
```

W1 and W2 will always have the same value (same base variable), and W3 and W4 will have the same value — but the two groups can vary independently.

## Self-Contained YAML Pattern (no circuit_type)

The newer YAML files (e.g. `circuits_yaml/3_stage_ring_osc_new.yaml`, `circuits_yaml/inverter_gf.yaml`) embed the full circuit definition **and** testbench directly in the YAML file. They omit `circuit_type` entirely, so the code path falls through to the default ngspice simulator in `iterative_ota_optimization.py` (after line 1001).

### How it works

```
YAML file
 ├── ota_subckt_template
 │   └── .subckt ... .ends  (the circuit definition with {placeholders})
 ├── testbench_template
 │   ├── {ota_subckt}        ← rendered subcircuit inserted here
 │   ├── X{name} {inst_pins} {subckt_name}   ← instance line
 │   └── .control ... .endc  ← simulation commands + .meas
 └── metric_post             ← expressions parse ngspice output
```

During rendering, the optimizer:
1. Reads `ota_subckt_template` and `testbench_template`
2. Builds `fmt` dict from `params`, variable mid-values, and `testbench_signals`
3. Renders `subckt_text = subckt_template.format(**fmt)`
4. Renders the full netlist: `testbench_template.format(ota_subckt=subckt_text, **fmt)`
5. Writes to `circuit_sim.spice` and runs ngspice

### Key requirements

- `ota_subckt_template` must contain a `.subckt`/`.ends` block using `{placeholder}` variables
- `testbench_template` must contain `{ota_subckt}` (insertion point) + an X instance line using `{inst_pins}` and `{subckt_name}`
- `subckt_pins` and `testbench_signals` define how pins map to testbench nets
- All ngspice `.meas` and `let` commands live inside the testbench template — no external sim module needed
- `metric_post` expressions parse named values printed by `print <name>` in the `.control` block

### Comparison: circuit_sim module vs self-contained

| Aspect | circuit_sim module | Self-contained YAML |
|--------|-------------------|---------------------|
| Circuit definition | Hard-coded in Python | In `ota_subckt_template` |
| Testbench | Hard-coded in Python | In `testbench_template` |
| Variable list | Matched to Python function signature | Matched to `${var}` in template |
| Adding new circuit | New Python file + YAML wiring | Only a new YAML file |
| Complexity | Higher (split across 2 files) | Lower (single file) |
| Flexibility | Can run Python logic pre-sim | Must express everything in SPICE |

### When to use each

**Use circuit_sim module** when the simulation requires Python-side logic: multi-corner sweeps, iterative calibration loops, conditional netlist generation, or complex feedback calculations that can't be expressed in ngspice `.meas` alone.

**Use self-contained YAML** for standard characterizations: single/multi-point DC, transient, AC, PWL sweeps — anything expressible entirely in SPICE `.control` blocks.

### Relationship with circuit_type dispatch

The dispatch at `iterative_ota_optimization.py:990-999` checks for special params (`v_ctrl_min/max` for VCOs) or explicit `circuit_type` to delegate to circuit_sim modules. If none match, the default template-rendering path at line 1005+ is used. Self-contained YAMLs rely on this fallthrough — they should **not** set `circuit_type` or VCO-specific params.

## YAML Key Reference

| Key | Required | Description |
|---|---|---|
| `variable` | Yes | Dict of optimization variables (values are null placeholders) |
| `W_values` | See note | Default list of allowed W values. Needed if any var starts with `W_` |
| `L_values` | See note | Default list of allowed L values. Needed if any var starts with `L_` |
| `{var}_values` | No | Per-variable override for a specific variable's allowed values |
| `width_scales` | No | Dict mapping scaled names to `[base_var, factor]` |
| `length_scales` | No | Dict mapping scaled names to `[base_var, factor]` |
| `params` | Yes | Fixed design parameters (vdd, vcm, ibias, etc.) |
| `user_specs` | Yes | Human-readable optimization goal |
| `user_specs_metric` | Yes | Machine-readable spec constraints |
| `ota_subckt_template` | Yes | SPICE subcircuit template with `{placeholder}` variables |
| `testbench_template` | Yes | SPICE testbench template with `{ota_subckt}` insertion point |
| `subckt_pins` | Conditional | Required for self-contained YAMLs (defines pin order for X instance) |
| `testbench_signals` | Conditional | Required for self-contained YAMLs (maps subckt pins to testbench nets) |
| `metric_post` | Yes | Metric formatting and FOM expression |
| `circuit_type` | No | Omit for self-contained YAMLs; set only when using a circuit_sim module |
