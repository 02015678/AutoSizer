#!/usr/bin/env python3
"""
Dry-run SPICE netlist generator for self-contained YAML circuits.

Usage:
    python dry_run.py -i circuits_yaml/3_stage_ring_osc_new.yaml -o output.spice

Picks the middle value for each variable and renders the netlist so you can
inspect it before running a full optimization.
"""

import argparse
import os
import sys
import yaml
import string


def _placeholders_in_format(fmt_string: str):
    """Extract {placeholder} names from a format string."""
    names = set()
    for _, field_name, _, _ in string.Formatter().parse(fmt_string):
        if field_name:
            names.add(field_name.split('!')[0].split(':')[0].split('.')[0])
    return names


def _find_values(config, var_name):
    """Find the value list for a variable (same priority logic as make_provider)."""
    # Priority 1: variable-specific {name}_values
    specific_key = f"{var_name}_values"
    if specific_key in config and config[specific_key]:
        return list(config[specific_key])

    # Priority 2: generic W_values / L_values
    if var_name.startswith(('W_', 'w_')):
        if 'W_values' in config:
            return list(config['W_values'])
    if var_name.startswith(('L_', 'l_')):
        if 'L_values' in config:
            return list(config['L_values'])

    return None


def _mid_value(values):
    """Return the element at len//2."""
    if not values:
        return None
    return values[len(values) // 2]


def _build_instantiation_pins(subckt_pins, testbench_signals):
    """Build the instance pin string by matching subckt pins to testbench signals."""
    connections = []
    for pin in subckt_pins:
        pin_upper = str(pin).upper()
        matched = False
        for expected_pin, signal in testbench_signals.items():
            if str(expected_pin).upper() == pin_upper:
                connections.append(str(signal))
                matched = True
                break
        if not matched:
            if pin_upper in ("0", "GND"):
                connections.append("0")
            else:
                connections.append(str(pin).lower() + "_internal")
    return ' '.join(connections)


def dry_run(config_path, output_path):
    with open(config_path, "r") as f:
        config = yaml.safe_load(f)

    # ----- validate required fields -----
    required_keys = ["ota_subckt_template", "testbench_template"]
    missing = [k for k in required_keys if k not in config]
    if missing:
        print(f"ERROR: Missing required keys in YAML: {missing}", file=sys.stderr)
        sys.exit(1)

    subckt_tmpl = config["ota_subckt_template"]
    tb_tmpl = config["testbench_template"]
    params = dict(config.get("params", {}))
    variable = dict(config.get("variable", {}))
    subckt_name = config.get("subckt_name", "SUBCKT")
    subckt_pins = list(config.get("subckt_pins", []))
    testbench_signals = dict(config.get("testbench_signals", {}))
    width_scales = dict(config.get("width_scales", {}))
    length_scales = dict(config.get("length_scales", {}))

    # ----- build fmt -----
    fmt = {}
    fmt.update(params)

    # Assign middle value for each variable
    for var_name in variable:
        values = _find_values(config, var_name)
        if values:
            mid = _mid_value(values)
            fmt[var_name] = mid
            print(f"  {var_name} = {mid}  (from {values})")
        else:
            print(f"  WARNING: no values found for '{var_name}', leaving as None", file=sys.stderr)
            fmt[var_name] = None

    fmt["pdk_lib_path"] = config.get("pdk_lib_path", "")
    fmt["subckt_name"] = subckt_name
    fmt.update(testbench_signals)

    # Instantiation pins
    fmt["inst_pins"] = _build_instantiation_pins(subckt_pins, testbench_signals)

    # width_scales / length_scales
    def _apply_scale(scale_dict, fmt, label):
        if not scale_dict:
            return
        for final_name, pair in scale_dict.items():
            if not (isinstance(pair, (list, tuple)) and len(pair) == 2):
                continue
            unit_key, factor = pair
            if unit_key in fmt and fmt[unit_key] is not None:
                fmt[final_name] = float(f'{fmt[unit_key] * factor:.4g}')

    _apply_scale(width_scales, fmt, "width")
    _apply_scale(length_scales, fmt, "length")

    # Special keys
    if 'pnp_model_path' in config:
        fmt['pnp_model_path'] = config['pnp_model_path']

    # ----- check missing placeholders -----
    all_placeholders = set()
    all_placeholders |= _placeholders_in_format(subckt_tmpl)
    all_placeholders |= _placeholders_in_format(tb_tmpl)
    all_placeholders.discard("ota_subckt")

    missing_placeholders = sorted(
        k for k in all_placeholders
        if k not in fmt or fmt[k] is None
    )

    if missing_placeholders:
        print(f"\nERROR: Missing placeholder values: {missing_placeholders}", file=sys.stderr)
        print(f"\nAvailable keys ({len(fmt)}):")
        for k in sorted(fmt.keys()):
            print(f"  - {k}: {fmt[k]}")
        sys.exit(1)

    # ----- render -----
    subckt_text = subckt_tmpl.format(**fmt)
    netlist = tb_tmpl.format(ota_subckt=subckt_text, **fmt)

    # ----- write output -----
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    with open(output_path, "w") as f:
        f.write(netlist)

    print(f"\nNetlist written to: {output_path}")
    print(f"  subckt lines: {len(subckt_text.splitlines())}")
    print(f"  total lines:  {len(netlist.splitlines())}")


def main():
    parser = argparse.ArgumentParser(description="Dry-run SPICE netlist generator")
    parser.add_argument("-i", "--input", required=True, help="Input YAML config file")
    parser.add_argument("-o", "--output", required=True, help="Output SPICE netlist path")
    args = parser.parse_args()

    if not os.path.exists(args.input):
        print(f"ERROR: Input file not found: {args.input}", file=sys.stderr)
        sys.exit(1)

    print(f"Reading: {args.input}")
    dry_run(args.input, args.output)


if __name__ == "__main__":
    main()
