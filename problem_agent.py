CIRCUIT_UNDERSTANDING_PROMPT = """
You are an expert analog circuit designer. Your task is to understand the circuit topology and the role of each component, with focus on the optimization variables.

**Circuit Name:** {subckt_name}

**Circuit Netlist:**
```
{ota_subckt_template}
```

**Fixed Design Parameters (DO NOT optimize):**
{params}

**Optimization Variables (WILL be optimized):**
{variables}

**Testbench:**
```
{testbench_template}
```

**Performance Metrics:**
The following metrics will be evaluated:
{metrics_list}

## Your Task - Part 1: Circuit Analysis

**IMPORTANT: Keep each main section to 3-5 sentences total. Focus on how the OPTIMIZATION VARIABLES affect circuit performance, not the fixed parameters.**

### 1. Circuit Topology Overview (3-5 sentences total)
Identify the circuit type from the netlist, describe the overall architecture, and explain the signal flow from input to output.

### 2. Optimization Variables Mapping (3-5 sentences total)
For each optimization variable, identify which transistors it controls (from the netlist and scaling rules). Group the variables by the functional role of transistors they control.

### 3. Optimization Variables Impact on Performance (3-5 sentences total for each sub-section)

{impact_section_text}

### 4. Variable Interactions (3-5 sentences total)
Describe any interactions between optimization variables: which must be scaled together for matching, which have conflicting effects, and which have synergistic effects.

### 5. Key Insights for Optimization (3-5 bullet points, 1 sentence each)
Provide 3-5 critical insights about optimizing this circuit, focusing on the optimization variables.

## Output Requirements

You must provide your analysis in **valid JSON format only**. Your response should contain ONLY the JSON object with no additional text before or after.

### JSON Structure
```json
{{
  "circuit_topology_overview": "3-5 sentences identifying the circuit type, describing the overall architecture, and explaining signal flow from input to output.",
  
  "optimization_variables_mapping": "3-5 sentences for each optimization variable, identifying which transistors it controls and grouping variables by functional role.",
  
  "optimization_variables_impact": {impact_json_str},
  
  "variable_interactions": "3-5 sentences describing interactions between optimization variables: which must be scaled together for matching, which have conflicting effects, and which have synergistic effects.",
  
  "key_insights_for_optimization": [
    "First critical insight about optimizing this circuit (1 sentence)",
    "Second critical insight about optimizing this circuit (1 sentence)",
    "Third critical insight about optimizing this circuit (1 sentence)",
    "Fourth critical insight (optional, 1 sentence)",
    "Fifth critical insight (optional, 1 sentence)"
  ]
}}
```

**CRITICAL REQUIREMENTS:**
1. Your entire response MUST be valid JSON - no markdown, no explanations, no text outside the JSON object
2. Do NOT wrap the JSON in code blocks or backticks
3. Each section should be 3-5 sentences focused on OPTIMIZATION VARIABLES
4. The "key_insights_for_optimization" array must contain 3-5 strings (bullet points as array elements)
5. All strings must properly escape special characters (quotes, newlines, etc.)

Focus your analysis on the OPTIMIZATION VARIABLES and their impact on performance.
"""


SEARCH_SPACE_REDUCTION_PROMPT = """
You are an expert analog circuit optimizer. Based on the circuit understanding analysis, your task is to rank all optimization variables by their impact on the target metric and determine the search space.

**Circuit Name:** {subckt_name}
**Optimization Target Metric:** {target_metric}
**Number of Variables to Actively Optimize:** {num_variables_to_optimize}
**Total Number of Variables:** {total_num_variables}

**Available Discrete Values:**
{variable_ranges}

**Scaling Rules (if applicable):**
{scaling_rules}

**Circuit Understanding Analysis:**

**Variable Impact on Metrics:**
{variable_impact_summary}

**Variable Interactions:**
{variable_interactions}

**Key Optimization Insights:**
{key_insights}


## Your Task - Part 2: Variable Prioritization and Search Space Configuration

Your task is to:

1. **Rank ALL optimization variables** (1 to {total_num_variables}) based on their impact on {target_metric}
2. **Select the top {num_variables_to_optimize} variables** to actively optimize
3. **For the top {num_variables_to_optimize} variables**: Provide sparse search ranges covering small to large values (3-5 values each, include smallest and largest from available list)
4. **For the remaining variables**: Determine appropriate fixed values (will NOT be optimized)

**Important Considerations:**
- Prioritize variables that have the strongest impact on {target_metric}
- Consider matching requirements and circuit topology
- Balance between search space size and coverage
- Fixed values should enable good performance while allowing optimized variables to have maximum impact

## Output Requirements

**CRITICAL JSON FORMATTING RULES - FOLLOW STRICTLY:**
1. Use ONLY double quotes (") for strings - NEVER single quotes (')
2. NO trailing commas before }} or ]
3. NO comments (// or /* */)
4. NO newlines inside string values - use spaces instead
5. Keep reasoning fields CONCISE (max 100 characters)
6. Escape any quotes inside strings with backslash: \\"
7. Response must be PURE JSON - no markdown, no code blocks, no extra text
8. Check every {{ has matching }} and every [ has matching ]

### JSON Structure
```json
{{
  "optimization_target": "copy target metric here",
  "num_variables_to_optimize": {num_variables_to_optimize},
  
  "variable_ranking": [
    {{
      "rank": <integer>,
      "variable": "<variable_name>",
      "impact_on_target": "<critical/high/medium/low>",
      "reasoning": "<CONCISE explanation max 100 chars - no newlines>"
    }}
  ],
  
  "optimization_configuration": {{
    "variables_to_optimize": {{
      "<variable_name>": {{
        "rank": <integer>,
        "search_space": [<value1>, <value2>, <value3>, ...],
        "num_choices": <integer>,
        "range_reasoning": "<CONCISE max 100 chars - no newlines>",
        "expected_behavior": "<CONCISE max 80 chars - no newlines>",
        "sensitivity": "<high/medium/low>"
      }}
    }},
    
    "variables_fixed": {{
      "<variable_name>": {{
        "rank": <integer>,
        "fixed_value": <numeric_value>,
        "fixed_reasoning": "<CONCISE max 100 chars - no newlines>",
        "why_this_value": "<CONCISE max 80 chars - no newlines>",
        "risk_if_suboptimal": "<low/medium/high>"
      }}
    }}
  }},
  
  "search_space_summary": {{
    "original_full_space": <integer>,
    "reduced_search_space": <integer>,
    "reduction_factor": <number>,
    "calculation": "<mathematical expression>",
    "explanation": "<CONCISE max 100 chars - no newlines>"
  }}
}}
```

**FINAL CHECKLIST BEFORE RESPONDING:**
- [ ] Response starts with {{ (no text before)
- [ ] Response ends with }} (no text after)
- [ ] All strings use double quotes, not single quotes
- [ ] No trailing commas
- [ ] All reasoning fields are under 100 characters
- [ ] No newlines inside any string values
- [ ] All {total_num_variables} variables are ranked
- [ ] Top {num_variables_to_optimize} variables have search_space arrays
- [ ] Remaining variables have fixed_value specified
- [ ] All values are from available sets

Provide ONLY the JSON response. No other text.
"""


CIRCUIT_REUNDERSTANDING_PROMPT = """You are an expert optimization advisor. RE-EVALUATE and REGENERATE the search space based on optimization results.

## CONTEXT
After {iterations_completed} iterations with {total_designs} designs evaluated, re-analyze the circuit and adapt the search strategy.

## CIRCUIT NETLIST (Re-analyze based on actual results)
```
{netlist}
```

**Task:** Re-analyze this circuit considering the optimization results below. Your previous understanding may have been incorrect.

## ORIGINAL SEARCH SPACE (Your Constraints)

### Available Optimization Variables
**You can ONLY optimize these variables (from YAML config):**
{available_variables}

### Fixed Parameters (CANNOT be changed to variables)
**CRITICAL: These parameters are PERMANENTLY FIXED and CANNOT be optimized or unfixed:**
{fixed_parameters}

**⚠️ WARNING: DO NOT use any variable names from the "Fixed Parameters" list above in your optimization_configuration. They are not available for optimization under any circumstances.**


### Current Search Ranges (DEFAULT — preserve these unless evidence warrants change)
**These are the ranges from the previous optimization cycle.** Start from these narrowed ranges.
Expand toward original ranges ONLY when Factor 6 expansion conditions are met (≥3 values spanned OR best not at boundary).
When uncertain, KEEP the current ranges rather than expanding.

{value_ranges}

**Original Full Search Space:** {original_search_space_size} combinations
**Maximum expansion boundary (original YAML ranges):**
{original_value_ranges}

## CURRENT CONFIGURATION

### Search Space Comparison
{search_space_comparison}

### Optimized Variables
{optimized_vars_section}

### Fixed Variables
{fixed_vars_section}

**Current Search Space:** {current_search_space} combinations

## OPTIMIZATION RESULTS

### Performance Progression
{progression_section}

### Convergence Status
- Status: {convergence_status}
- Assessment: {convergence_reason}
- Best FOM: {best_fom}
- Stagnant: {stagnant}

### Variable Impact
{impact_section}

### Issues Detected
{issues_section}

### Top Designs
{top_designs_section}

## YOUR TASK
Decide on action based on the results:
- `continue_current`: Keep current configuration (if performing well and not stagnant)
- `expand_ranges`: Expand variable ranges (when top designs span >=3 values OR best value is NOT at a boundary)
- `narrow_ranges`: Narrow to promising regions (when >=80% of top-10 designs share the same boundary value AND relationship is monotonic)
- `unfix_variables`: Unfix some fixed variables (if stagnation or suboptimal)
- `change_focus`: Swap optimized/fixed variables (if optimized var has low impact)
- `converged`: Optimization complete (only if target met or exhausted search)

## DECISION GUIDELINES

**Expand ranges:**
- Top designs span >=3 different values for a variable (genuine trade-off exists)
- Best value is NOT at a boundary (room for further improvement in both directions)
- FOM improvements still occurring with current range
- Action: Add 2-3 adjacent values from original ranges on each boundary

**Unfix variables:**
- Stagnation detected (no improvement for 2+ iterations)
- Current optimized variables explored adequately
- Suspect interactions between fixed and optimized variables
- Fixed variable's current value appears suboptimal based on data
- Action: Unfix 1-2 most promising fixed variables with 3-5 values
- Priority: Unfix before declaring convergence, but not before preserving validated narrowing

**Narrow ranges:**
- >=80% of top-10 designs share the same value for a variable
- That value is at a boundary (minimum or maximum) of the current allowed range
- The relationship appears monotonic (e.g., "always smaller -> better FOM")
- Action: Fix the variable at the dominant boundary value (single value)
- Note: The inner optimization loop may have already narrowed this variable based on data. Preserve such narrowing unless new evidence contradicts it.

**Change focus:** 
- Optimized variable shows minimal variance in top designs (>70% same value)
- All top designs converged to 1-2 values for a variable
- Action: Fix the converged variable, unfix a new one
- Enables exploring new dimensions

**Continue current:** 
- Making steady progress (improvement in last iteration)
- Haven't explored current space adequately (< 40% of combinations)
- No clear issues with current configuration
- Use temporarily, but bias toward taking action

**Converged (LAST RESORT):**
- Best FOM exceeds or meets target specification
- Improvements < 1% for 4+ consecutive iterations
- All promising variables explored with expanded ranges
- Fixed variables already unfixed and tested
- Warning: Only declare convergence after aggressive exploration

## DECISION PRIORITY ORDER
1. **Change focus** - if optimized variable converged but performance suboptimal
2. **Expand ranges** - if top designs span >=3 values OR best not at boundary (matches inner loop Factor 6 criteria)
3. **Narrow ranges** - if >=80% boundary dominance + monotonic (matches inner loop Factor 6 criteria)
4. **Unfix variables** - if stagnation and fixed variable likely suboptimal
5. **Continue current** - if making progress and space not fully explored
6. **Converged** - only after exhausting all options

## CRITICAL CONSTRAINTS
1. **ONLY use variables from "Available Optimization Variables" section above**
2. **Start from the "Current Search Ranges" — these are the narrowed ranges from the previous cycle**
3. **DO NOT create new variables like 'L' if they are fixed parameters**
4. **You may expand up to the original YAML ranges (shown as "Maximum expansion boundary"), but only when Factor 6 expansion conditions are met**
6. **Each variable: 3-5 discrete values (smallest and largest always included)**
7. **Variables in "Fixed Parameters" section CAN be unfixed and optimized**
8. **Bias: When uncertain, KEEP current ranges. Narrow only when Factor 6 conditions are met (>=80% boundary dominance + monotonic). Expand only when Factor 6 expansion conditions are met (>=3 values spanned OR best not at boundary).**

## SEARCH SPACE PHILOSOPHY
- **Respect inner-loop narrowing**: The inner optimization loop may have already narrowed variables based on Factor 6 criteria (>=80% boundary dominance + monotonic). Preserve these narrowings unless new evidence explicitly contradicts them.
- **Data-driven decisions**: Use Factor 6 criteria for both narrowing and expansion. Narrow when >=80% boundary dominance + monotonic. Expand when >=3 values spanned OR best not at boundary.
- **Default to KEEP**: When uncertain, maintain current ranges rather than expanding or narrowing.
- **Avoid both premature narrowing AND premature expansion**: A false narrowing wastes a few simulations. A missed narrowing wastes dozens. But a false expansion also wastes simulations on values the data has already ruled out.

## VALIDATION CHECKLIST (Check before responding)
- [ ] All variable names are from "Available Optimization Variables"
- [ ] All values are subsets of original YAML ranges (shown as "Maximum expansion boundary")
- [ ] No fixed parameters (like L, vdd) are being optimized
- [ ] Total optimized variables: 3-4
- [ ] Each variable has 3-5 values
- [ ] JSON is valid (no trailing commas, proper quotes)

## JSON FORMAT RULES
Your response must be VALID JSON only:
1. Use double quotes (") for all strings, never single quotes (')
2. No trailing commas before }} or ]
3. No comments (// or /* */)
4. All string values must be properly escaped
5. Check that every {{ has a matching }} and every [ has a matching ]
6. Do not include any text before {{ or after }}

CRITICAL: Your response must be VALID JSON only. Follow these rules strictly:
1. Return ONLY valid, complete JSON
2. Ensure ALL string values have closing quotes
3. Ensure ALL objects are properly closed with }}
4. Keep explanatory text CONCISE (max 100 words per field)
5. If running out of space, abbreviate explanations but COMPLETE the JSON structure

## OUTPUT (JSON ONLY)

{{{{
  "optimization_target": "{target_metric}",
  "regeneration_reasoning": "<why regenerating>",
  "action_taken": "<action>",
  "changes_from_previous": "<summary of changes>",
  
  "variable_ranking": [
    {{{{
      "rank": <1-n>,
      "variable": "<name>",
      "impact_on_target": "<critical|high|medium|low>",
      "reasoning": "<explanation>"
    }}}}
  ],
  
  "optimization_configuration": {{{{
    "variables_to_optimize": {{{{
      "<variable_name>": {{{{
        "rank": <rank>,
        "search_space": [<values>],
        "num_choices": <number>,
        "range_reasoning": "<why this range>",
        "expected_behavior": "<how target changes>",
        "sensitivity": "<high|medium|low>",
        "change_from_previous": "<what changed>"
      }}}}
    }}}},
    
    "variables_fixed": {{{{
      "<variable_name>": {{{{
        "rank": <rank>,
        "fixed_value": <value>,
        "fixed_reasoning": "<why fixed>",
        "why_this_value": "<why this value>",
        "risk_if_suboptimal": "<risk>",
        "change_from_previous": "<what changed>"
      }}}}
    }}}}
  }}}},
  
  "search_space_summary": {{{{
    "original_full_space": <total>,
    "reduced_search_space": <total>,
    "reduction_factor": "<factor>",
    "change_factor": "<expansion or reduction>",
    "calculation": "<calculation>",
    "explanation": "<explanation>"
  }}}},
  
  "expected_improvement": "<expected gain>",
  "confidence": "<high|medium|low>"
}}}}

Return ONLY JSON based on data.
"""



# CIRCUIT_REUNDERSTANDING_PROMPT = """You are an expert optimization advisor. RE-EVALUATE and REGENERATE the search space based on optimization results.

# ## CONTEXT

# After {iterations_completed} iterations with {total_designs} designs evaluated, re-analyze the circuit and adapt the search strategy.

# ## CIRCUIT NETLIST (Re-analyze based on actual results)

# ```
# {netlist}
# ```

# **Task:** Re-analyze this circuit considering the optimization results below. Your previous understanding may have been incorrect.

# ## CURRENT CONFIGURATION

# **Optimized Variables:**
# {optimized_vars_section}

# **Fixed Variables:**
# {fixed_vars_section}

# **Current Search Space:** {current_search_space} combinations

# ## OPTIMIZATION RESULTS

# ### Performance Progression
# {progression_section}

# ### Convergence Status
# - Status: {convergence_status}
# - Assessment: {convergence_reason}
# - Best FOM: {best_fom}
# - Stagnant: {stagnant}

# ### Variable Impact
# {impact_section}

# ### Issues Detected
# {issues_section}

# ### Top Designs
# {top_designs_section}
# ## YOUR TASK

# Decide on action:
# - `continue_current`: Keep configuration
# - `expand_ranges`: Expand variable ranges
# - `narrow_ranges`: Narrow to promising regions
# - `unfix_variables`: Unfix some fixed variables
# - `change_focus`: Swap optimized/fixed variables
# - `converged`: Optimization complete

# ## GUIDELINES

# **Expand ranges:** 40%+ of top designs at boundaries
# **Narrow ranges:** Top designs cluster in middle
# **Unfix variables:** Fixed variable limiting performance or stagnation
# **Change focus:** Optimized variable shows minimal impact

# ## CONSTRAINTS

# Available values: {width_values}
# Optimize: 3-4 variables
# Each variable: 3-7 discrete values


# CRITICAL: Your response must be VALID JSON only. Follow these rules strictly:
# 1. Use double quotes (") for all strings, never single quotes (')
# 2. No trailing commas before }} or ]
# 3. No comments (// or /* */)
# 4. All string values must be properly escaped
# 5. Check that every {{ has a matching }} and every [ has a matching ]
# 6. Do not include any text before {{ or after }}

# ## OUTPUT (JSON ONLY)

# {{{{
#   "optimization_target": "{target_metric}",
#   "regeneration_reasoning": "<why regenerating>",
#   "action_taken": "<action>",
#   "changes_from_previous": "<summary of changes>",
  
#   "variable_ranking": [
#     {{{{
#       "rank": <1-n>,
#       "variable": "<name>",
#       "impact_on_target": "<critical|high|medium|low>",
#       "reasoning": "<explanation>"
#     }}}}
#   ],
  
#   "optimization_configuration": {{{{
#     "variables_to_optimize": {{{{
#       "<variable_name>": {{{{
#         "rank": <rank>,
#         "search_space": [<values>],
#         "num_choices": <number>,
#         "range_reasoning": "<why this range>",
#         "expected_behavior": "<how target changes>",
#         "sensitivity": "<high|medium|low>",
#         "change_from_previous": "<what changed>"
#       }}}}
#     }}}},
    
#     "variables_fixed": {{{{
#       "<variable_name>": {{{{
#         "rank": <rank>,
#         "fixed_value": <value>,
#         "fixed_reasoning": "<why fixed>",
#         "why_this_value": "<why this value>",
#         "risk_if_suboptimal": "<risk>",
#         "change_from_previous": "<what changed>"
#       }}}}
#     }}}}
#   }}}},
  
#   "search_space_summary": {{{{
#     "original_full_space": <total>,
#     "reduced_search_space": <total>,
#     "previous_space": {previous_space},
#     "reduction_factor": "<factor>",
#     "change_factor": "<expansion or reduction>",
#     "calculation": "<calculation>",
#     "explanation": "<explanation>"
#   }}}},
  
#   "expected_improvement": "<expected gain>",
#   "confidence": "<high|medium|low>"
# }}}}

# Return ONLY JSON based on data.
# """

