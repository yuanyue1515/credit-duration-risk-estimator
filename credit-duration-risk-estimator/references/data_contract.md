# Data Contract

## Required raw columns

The existing notebook expects a flat table with at least these columns:

- `dt`: snapshot date
- `vntg_mth`: vintage month
- `loan_age_m`: loan age bucket
- `mob`: months on book
- `risk_inst`: disbursement amount, used for `mob = 0`
- `risk_outsd`: outstanding balance used for roll-rate and balance forecast

## Configurable dimensions

Do not treat current fields as fixed. The skill should support a configurable dimension schema so other teams can reuse it without changing code.

Recommended config shape:

```json
{
  "dimension_aliases": {
    "channel": "loan_belong_tag",
    "project": "project_total",
    "segment": "customer_segment"
  },
  "dimension_value_maps": {
    "channel": {
      "端外生态团队": "线上",
      "线下团队": "线下"
    }
  },
  "filter_dimensions": {
    "channel": ["线上"],
    "project": ["A项目", "B项目"],
    "segment": ["新客"]
  },
  "group_by_fields": ["channel", "project", "segment"]
}
```

Interpretation:

- `dimension_aliases`: logical name to source column name
- `dimension_value_maps`: optional regrouping from raw values to shared labels
- `filter_dimensions`: filtering conditions using logical names
- `group_by_fields`: output granularity using logical names

Support at least these filter styles:

- exact single value
- multi-value inclusion list
- numeric or date range
- null/non-null constraint

If the user does not provide configuration, fall back to direct raw-field filtering.

## Required configuration inputs

- `start_dt`: start of vintage month range
- `end_dt`: end of vintage month range
- planned future disbursement for months beyond observed data
- dimension config: aliases, value maps, filters, and group-by fields
- roll-rate aggregation mode: weighted average or arithmetic average
- monthly adjustment coefficient table
- `max_loan_age_bucket`: maximum finite loan-age bucket, default `7`
- `bad_debt_start_age`: first loan-age bucket counted as bad debt, default `4`
- tail recovery ratio
- tail-bucket logic mode: normal or high-period fallback

## Derived matrices

Construct:

- `MOB0_risk_inst`: pivot of `risk_inst` for `mob = 0`
- `risk_oustd0 ... risk_oustdN`: pivot of `risk_outsd` by `loan_age_m = 0..N`
- `risk_oustdNplus`: pivot of `risk_outsd` by `loan_age_m = N+`

Each matrix should:

- use `vntg_mth` as row index
- use `mob` as columns
- be reindexed to the full date range and full `0..20` mob range used by the notebook

For the tail-bucket matrix, keep a separate real-value availability mask so the execution logic can distinguish:

- real observed tail-bucket cells that must be preserved
- future missing tail-bucket cells that may be filled by recursive accumulation

## Output sheets

The notebook exports these sheets:

- `平均余额`
- `久期`
- `全生命周期坏账`
- `年化风险`
- `放款不良率`

It also merges them into a single `output_merged.xlsx`.

## Practical notes

- The notebook currently reads a local CSV such as `0802.csv`; the skill should not assume that exact file exists.
- `loan_age_m` and `mob` are rounded before use.
- Clean invalid `loan_age_m / mob` combinations before aggregation:
  - bucket `0` allows `mob >= 0`
  - bucket `1` allows `mob >= 1`
  - ...
  - bucket `N` allows `mob >= N`
  - tail bucket `N+` only allows `mob >= N+1`
- Drop invalid rows rather than reallocating them to another bucket.
- If `dt` is not month-end for the latest month, use the latest observed snapshot directly and do not extrapolate by day count before building `rollrate0`.
- Apply dimension mapping before pivoting so grouped outputs stay consistent across teams.
- When multiple logical dimensions map to missing raw fields, fail fast with a clear message naming the missing source columns.
- If the user only supplies aggregated result tables and not raw balance data, say that notebook-level reproduction is not possible and switch to an approximation explicitly.
- For handoff, prefer giving users [input_table_template.csv](input_table_template.csv) and [input_template.md](input_template.md) together.
