import os
import subprocess
import re
import math
import numpy as np
import itertools
from dataclasses import dataclass
from typing import Optional, List, Dict, Tuple
import json
from datetime import datetime

# Result dataclass
@dataclass
class VCOResult:
    # Size variables
    W_inv_p: float
    W_inv_n: float
    L_inv_p: float
    L_inv_n: float

    # Performance metrics
    freq_hz: float
    power_uw: float
    v_ctrl: float

    # Optional metrics (populated if sweep performed)
    freq_at_min_v: float = None
    freq_at_max_v: float = None
    tuning_range_percent: float = None
    vco_gain_MHz_per_V: float = None


def simulate_vco(pdk_lib_path,
                 W_inv_p_base, W_inv_n_base,
                 L_inv_p_base, L_inv_n_base,
                 v_ctrl_min=0.0, v_ctrl_max=1.8,
                 num_v_ctrl_points=5,
                 vdd=1.8, temp=27,
                 results_dir='./results',
                 **kwargs):
    """
    Simulate VCO with characterization sweep
    Returns VCOResult with tuning range, gain, etc.
    """
    
    # Convert parameters
    W_inv_p = float(W_inv_p_base)
    W_inv_n = float(W_inv_n_base)
    L_inv_p = float(L_inv_p_base)
    L_inv_n = float(L_inv_n_base)
    
    # Run characterization sweep
    v_ctrl_vals = np.linspace(v_ctrl_min, v_ctrl_max, num_v_ctrl_points)
    
    frequencies = []
    powers = []
    valid_v_ctrl = []
    
    sim_dir = os.path.join(results_dir, 'ngspice_sim')
    os.makedirs(sim_dir, exist_ok=True)
    
    original_dir = os.getcwd()
    os.chdir(sim_dir)
    
    try:
        for v_ctrl in v_ctrl_vals:
            # Single point simulation
            result_single = _simulate_vco_single_point(
                pdk_lib_path, W_inv_p, W_inv_n, L_inv_p, L_inv_n,
                v_ctrl, vdd, temp
            )
            
            if result_single and result_single.freq_hz > 0:
                frequencies.append(result_single.freq_hz)
                powers.append(result_single.power_uw)
                valid_v_ctrl.append(v_ctrl)
        
        if len(frequencies) < 2:
            return None
        
        # Calculate characterization metrics
        frequencies = np.array(frequencies)
        powers = np.array(powers)
        valid_v_ctrl = np.array(valid_v_ctrl)
        
        freq_at_min = frequencies[0]
        freq_at_max = frequencies[-1]
        f_center = (freq_at_max + freq_at_min) / 2
        tuning_range_percent = (freq_at_max - freq_at_min) / f_center * 100
        
        # Average power
        power_uw = np.mean(powers)
        
        # VCO gain (MHz/V)
        local_gains = np.diff(frequencies) / np.diff(valid_v_ctrl) / 1e6
        vco_gain_MHz_per_V = np.mean(local_gains)
        
        return VCOResult(
            W_inv_p=W_inv_p,
            W_inv_n=W_inv_n,
            L_inv_p=L_inv_p,
            L_inv_n=L_inv_n,
            freq_hz=f_center,
            power_uw=power_uw,
            v_ctrl=(v_ctrl_min + v_ctrl_max) / 2,
            freq_at_min_v=freq_at_min,
            freq_at_max_v=freq_at_max,
            tuning_range_percent=tuning_range_percent,
            vco_gain_MHz_per_V=vco_gain_MHz_per_V
        )
    
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        return None
        
    finally:
        os.chdir(original_dir)


def _simulate_vco_single_point(pdk_lib_path, W_inv_p, W_inv_n, L_inv_p, L_inv_n,
                                v_ctrl, vdd, temp):
    """Helper: simulate VCO at single control voltage point"""
    
    num_stages = 5
    tran_file = f'vco_tran_{v_ctrl:.3f}.txt'
    
    # Build netlist
    netlist_stages = ""
    for i in range(num_stages):
        current_node = f"N{i}"
        next_node = f"N{(i+1) % num_stages}"
        
        # Stage 0 slightly weaker to break symmetry
        if i == 0:
            w_p = W_inv_p * 0.9
            w_n = W_inv_n
        else:
            w_p = W_inv_p
            w_n = W_inv_n
        
        netlist_stages += f"""
XMPINV{i} {next_node} {current_node} VDD VDD sky130_fd_pr__pfet_01v8 l={L_inv_p} w={w_p} m=1
XMNINV{i} {next_node} {current_node} GND GND sky130_fd_pr__nfet_01v8 l={L_inv_n} w={w_n} m=1
"""
    
    # Variable capacitance
    cap_base = 5e-15
    cap_variable = (v_ctrl / vdd) * 50e-15
    cap_total = cap_base + cap_variable
    
    netlist = f"""* VCO Single Point
.lib {pdk_lib_path} tt
.global VDD GND
.temp {temp}

{netlist_stages}

C0 N0 GND {cap_total}
C1 N1 GND {cap_total}
C2 N2 GND {cap_total}
C3 N3 GND {cap_total}
C4 N4 GND {cap_total}

VSUP VDD GND PWL(0 0 10n {vdd})
RBREAK N0 GND 1G

.control
tran 1n 20u
print v(N3) > {tran_file}
meas tran avg_power AVG p(VSUP) from=15u to=20u
quit
.endc
.end
"""
    
    netlist_path = f'vco_sim_{v_ctrl:.3f}.spice'
    with open(netlist_path, 'w') as f:
        f.write(netlist)
    
    try:
        result = subprocess.run(
            ['ngspice', '-b', netlist_path],
            capture_output=True,
            text=True,
            timeout=60
        )
        
        if result.returncode != 0:
            return None
        
        # Parse frequency
        freq = None
        if os.path.exists(tran_file):
            time_vals = []
            volt_vals = []
            
            with open(tran_file, 'r') as f:
                lines = f.readlines()
            
            for line in lines:
                line = line.strip()
                if not line or 'Index' in line or 'v(n3)' in line or '---' in line:
                    continue
                
                parts = line.split()
                if len(parts) >= 3:
                    try:
                        t = float(parts[1])
                        v = float(parts[2])
                        if t >= 10e-6:
                            time_vals.append(t)
                            volt_vals.append(v)
                    except:
                        continue
            
            if len(volt_vals) > 20:
                v_arr = np.array(volt_vals)
                t_arr = np.array(time_vals)
                
                v_mid = vdd / 2
                crossings = np.where(np.diff(np.sign(v_arr - v_mid)) > 0)[0]
                
                if len(crossings) >= 3:
                    periods = np.diff(t_arr[crossings])
                    avg_period = np.mean(periods)
                    freq = 1.0 / avg_period
        
        # Parse power
        power_uw = 0.0
        for line in result.stdout.split('\n'):
            if 'avg_power' in line.lower():
                parts = line.split('=')
                if len(parts) >= 2:
                    try:
                        power_w = float(parts[-1].strip().split()[0])
                        power_uw = abs(power_w) * 1e6
                    except:
                        pass
        
        if freq is None:
            return None
        
        return VCOResult(
            W_inv_p=W_inv_p, W_inv_n=W_inv_n,
            L_inv_p=L_inv_p, L_inv_n=L_inv_n,
            freq_hz=freq, power_uw=power_uw, v_ctrl=v_ctrl
        )

    except Exception as e:
        print(f"Simulation error: {e}")
        import traceback

        traceback.print_exc()
        return None
