# Credit Duration Risk Estimator

A Codex skill and notebook-aligned Python script for estimating:

- average balance
- duration
- annualized risk
- lifetime bad debt
- lending bad rate

The implementation is designed for vintage / MOB / loan-age based credit portfolios and follows a month-end roll-rate methodology commonly used in consumer lending analysis.

## What It Does

This project helps reproduce a month-end rolling model from a flat source table and supports:

- notebook-style balance recursion
- configurable product term
- configurable MOB horizon
- configurable loan-age bucket structure such as `0..7 + 7+` or `0..4 + 4+`
- configurable bad-debt start age
- grouped reruns by business dimension
- weighted-average and arithmetic-average style roll-rate estimation paths

## Core Outputs

For each vintage month, the script produces:

- `平均余额`
- `久期`
- `年化风险`
- `全生命周期坏账`
- `放款不良率`
- `MOB0放款额`

## Input Data

The main script expects one flat CSV with at least these columns:

- `dt`
- `vntg_mth`
- `loan_age_m`
- `mob`
- `risk_inst`
- `risk_outsd`

Typical optional columns can be used for filtering or grouped reruns, for example:

- channel
- project
- segment
- strategy tag
- any custom business dimension

See:

- [references/input_template.md](references/input_template.md)
- [references/input_table_template.csv](references/input_table_template.csv)

## Main Parameters

The main script is:

- [scripts/run_rollrate_notebook.py](scripts/run_rollrate_notebook.py)

Common parameters:

- `--product-term`
  - default `12`
- `--mob-max`
  - default `20`
  - usually follows:
    `mob_max = 产品期数 + 最大逾期账龄`
- `--max-loan-age-bucket`
  - default `7`
  - means the model uses `0..7 + 7+`
  - if your data is `0..4 + 4+`, set it to `4`
- `--bad-debt-start-age`
  - default `4`
  - matches the common `M3+` definition
- `--recovery-rate-7plus`
  - default `1.0`

## Example Usage

Default overall run:

```bash
python scripts/run_rollrate_notebook.py input.csv \
  --output-overall output_overall.csv
```

Run with grouped output:

```bash
python scripts/run_rollrate_notebook.py input.csv \
  --group-field segment_l1 \
  --output-overall output_overall.csv \
  --output-group output_group.csv
```

Run with `0..4 + 4+` loan-age buckets:

```bash
python scripts/run_rollrate_notebook.py input.csv \
  --max-loan-age-bucket 4 \
  --bad-debt-start-age 4 \
  --output-overall output_overall.csv
```

Run for an 18-term product:

```bash
python scripts/run_rollrate_notebook.py input.csv \
  --product-term 18 \
  --mob-max 26 \
  --output-overall output_overall.csv
```

## Business Logic Notes

- Average balance is calculated with a product-term-sensitive denominator:
  `product_term + 0.5`
- Duration is calculated as:
  `产品期数 * 平均余额 / MOB0放款额`
- Lifetime bad debt, annualized risk, and lending bad rate are read at:
  `mob_max - 1`
- Invalid `loan_age_m / mob` combinations are dropped before modeling
- Real observed cells are preserved where available; only missing future cells are recursively filled

For detailed logic, see:

- [references/rolling_model.md](references/rolling_model.md)
- [SKILL.md](SKILL.md)

## Project Structure

```text
credit-duration-risk-estimator/
├── SKILL.md
├── README.md
├── scripts/
│   ├── run_rollrate_notebook.py
│   └── estimate_credit_risk.py
└── references/
    ├── data_contract.md
    ├── input_template.md
    ├── input_table_template.csv
    ├── dimension_config_example.json
    └── rolling_model.md
```

## Notes

- The primary production path is `run_rollrate_notebook.py`
- `estimate_credit_risk.py` is only a simplified fallback and does not replace the notebook-aligned roll-rate model
- This repository focuses on deterministic reruns from source tables, not on generic PD/LGD modeling
