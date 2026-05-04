#!/usr/bin/env python3
import argparse
import json
from pathlib import Path


def periodic_rate(annual_rate, months_per_period):
    if annual_rate <= -1:
        raise ValueError("annual rate must be greater than -1")
    return (1 + annual_rate) ** (months_per_period / 12.0) - 1


def periodic_probability(annual_probability, months_per_period):
    if not 0 <= annual_probability <= 1:
        raise ValueError("annual probability must be between 0 and 1")
    return 1 - (1 - annual_probability) ** (months_per_period / 12.0)


def build_schedule(asset):
    if "schedule" in asset:
        schedule = asset["schedule"]
        if not isinstance(schedule, list) or not schedule:
            raise ValueError("schedule must be a non-empty list")
        normalized = []
        for idx, row in enumerate(schedule, start=1):
            normalized.append(
                {
                    "period": idx,
                    "months": float(row.get("months", asset.get("payment_frequency_months", 1))),
                    "begin_balance": float(row["begin_balance"]),
                    "scheduled_principal": float(row.get("scheduled_principal", 0.0)),
                    "interest": float(row.get("interest", 0.0)),
                    "total_payment": float(
                        row.get(
                            "total_payment",
                            float(row.get("scheduled_principal", 0.0)) + float(row.get("interest", 0.0)),
                        )
                    ),
                }
            )
        return normalized

    principal = float(asset["principal"])
    annual_rate = float(asset.get("annual_rate", 0.0))
    term_months = int(asset["term_months"])
    freq = int(asset.get("payment_frequency_months", 1))
    amortization_type = asset.get("amortization_type", "equal_payment")
    if term_months <= 0 or freq <= 0 or term_months % freq != 0:
        raise ValueError("term_months must be positive and divisible by payment_frequency_months")

    periods = term_months // freq
    rate = periodic_rate(annual_rate, freq)
    balance = principal
    schedule = []

    if amortization_type == "bullet":
        for period in range(1, periods + 1):
            interest = balance * rate
            principal_payment = balance if period == periods else 0.0
            total_payment = interest + principal_payment
            schedule.append(
                {
                    "period": period,
                    "months": float(freq),
                    "begin_balance": balance,
                    "scheduled_principal": principal_payment,
                    "interest": interest,
                    "total_payment": total_payment,
                }
            )
    elif amortization_type == "equal_principal":
        principal_payment = principal / periods
        for period in range(1, periods + 1):
            interest = balance * rate
            scheduled_principal = principal_payment if period < periods else balance
            total_payment = interest + scheduled_principal
            schedule.append(
                {
                    "period": period,
                    "months": float(freq),
                    "begin_balance": balance,
                    "scheduled_principal": scheduled_principal,
                    "interest": interest,
                    "total_payment": total_payment,
                }
            )
            balance = max(balance - scheduled_principal, 0.0)
    elif amortization_type == "equal_payment":
        if abs(rate) < 1e-12:
            payment = principal / periods
        else:
            payment = principal * rate / (1 - (1 + rate) ** (-periods))
        for period in range(1, periods + 1):
            interest = balance * rate
            scheduled_principal = payment - interest
            if period == periods:
                scheduled_principal = balance
                payment = interest + scheduled_principal
            schedule.append(
                {
                    "period": period,
                    "months": float(freq),
                    "begin_balance": balance,
                    "scheduled_principal": scheduled_principal,
                    "interest": interest,
                    "total_payment": payment,
                }
            )
            balance = max(balance - scheduled_principal, 0.0)
    else:
        raise ValueError("unsupported amortization_type")

    return schedule


def estimate(asset):
    schedule = build_schedule(asset)
    principal = float(asset["principal"])
    carrying_amount = float(asset.get("price", asset.get("market_value", principal)))
    discount_rate_annual = float(asset.get("discount_rate_annual", asset.get("annual_rate", 0.0)))
    default_rate_annual = float(asset.get("default_rate_annual", 0.0))
    prepayment_rate_annual = float(asset.get("prepayment_rate_annual", 0.0))
    lgd = float(asset.get("loss_given_default", 0.0))
    recovery_lag_months = int(asset.get("recovery_lag_months", 0))

    survival = 1.0
    surviving_balance = principal
    elapsed_months = 0.0
    pv_total = 0.0
    pv_time_weighted = 0.0
    wal_numerator = 0.0
    principal_collected = 0.0
    expected_loss = 0.0
    annual_rate_income = 0.0
    period_rows = []

    for row in schedule:
        months = float(row["months"])
        elapsed_months += months
        years = elapsed_months / 12.0
        begin_balance = float(row["begin_balance"])
        scheduled_principal = max(float(row["scheduled_principal"]), 0.0)
        interest = float(row["interest"])

        period_default_prob = periodic_probability(default_rate_annual, months)
        period_prepay_prob = periodic_probability(prepayment_rate_annual, months)

        contractual_balance_factor = begin_balance / principal if principal > 0 else 0.0
        exposure_at_default = min(surviving_balance, begin_balance * survival, principal * contractual_balance_factor * survival)
        period_expected_loss = exposure_at_default * period_default_prob * lgd
        expected_loss += period_expected_loss

        performing_balance_after_default = exposure_at_default * (1 - period_default_prob)
        scheduled_principal_surviving = min(scheduled_principal, performing_balance_after_default)
        interest_surviving = interest * survival * (1 - period_default_prob)

        remaining_after_scheduled = max(performing_balance_after_default - scheduled_principal_surviving, 0.0)
        prepayment_amount = remaining_after_scheduled * period_prepay_prob
        realized_principal = scheduled_principal_surviving + prepayment_amount
        realized_total_cash = scheduled_principal_surviving + interest_surviving + prepayment_amount

        discount_factor = (1 + discount_rate_annual) ** years
        pv_cash = realized_total_cash / discount_factor
        pv_total += pv_cash
        pv_time_weighted += years * pv_cash
        wal_numerator += years * realized_principal
        principal_collected += realized_principal
        annual_rate_income += interest_surviving

        surviving_balance = max(remaining_after_scheduled - prepayment_amount, 0.0)
        next_contractual_balance = max(begin_balance - scheduled_principal, 0.0)
        survival = surviving_balance / next_contractual_balance if next_contractual_balance > 1e-12 else 0.0
        survival = max(min(survival, 1.0), 0.0)

        period_rows.append(
            {
                "period": row["period"],
                "year": round(years, 6),
                "begin_balance": round(begin_balance, 6),
                "scheduled_principal": round(scheduled_principal, 6),
                "interest": round(interest, 6),
                "period_default_prob": round(period_default_prob, 8),
                "period_prepay_prob": round(period_prepay_prob, 8),
                "survival_ratio": round(survival, 8),
                "realized_principal": round(realized_principal, 6),
                "realized_total_cash": round(realized_total_cash, 6),
                "expected_loss": round(period_expected_loss, 6),
            }
        )

    wal_years = wal_numerator / principal_collected if principal_collected > 0 else 0.0
    macaulay_duration_years = pv_time_weighted / pv_total if pv_total > 0 else 0.0
    first_period_discount_rate = periodic_rate(discount_rate_annual, float(schedule[0]["months"]))
    modified_duration_years = (
        macaulay_duration_years / (1 + first_period_discount_rate) if first_period_discount_rate > -1 else 0.0
    )
    expected_loss_ratio = expected_loss / carrying_amount if carrying_amount > 0 else 0.0
    annualized_expected_loss_rate = expected_loss_ratio / wal_years if wal_years > 0 else 0.0
    gross_annual_income_rate = annual_rate_income / carrying_amount / wal_years if wal_years > 0 and carrying_amount > 0 else 0.0
    risk_adjusted_annual_return = gross_annual_income_rate - annualized_expected_loss_rate

    return {
        "input_summary": {
            "principal": principal,
            "carrying_amount": carrying_amount,
            "default_rate_annual": default_rate_annual,
            "loss_given_default": lgd,
            "prepayment_rate_annual": prepayment_rate_annual,
            "discount_rate_annual": discount_rate_annual,
            "recovery_lag_months": recovery_lag_months,
        },
        "results": {
            "wal_years": round(wal_years, 6),
            "macaulay_duration_years": round(macaulay_duration_years, 6),
            "modified_duration_years": round(modified_duration_years, 6),
            "expected_loss_amount": round(expected_loss, 6),
            "expected_loss_ratio": round(expected_loss_ratio, 8),
            "annualized_expected_loss_rate": round(annualized_expected_loss_rate, 8),
            "gross_annual_income_rate": round(gross_annual_income_rate, 8),
            "risk_adjusted_annual_return": round(risk_adjusted_annual_return, 8),
        },
        "period_rows": period_rows,
    }


def aggregate_pool(assets):
    if not assets:
        raise ValueError("assets must be a non-empty list")

    asset_results = []
    total_principal = 0.0
    total_carrying_amount = 0.0
    weighted_wal = 0.0
    weighted_macaulay = 0.0
    weighted_modified = 0.0
    total_expected_loss = 0.0
    total_annual_income = 0.0

    for idx, asset in enumerate(assets, start=1):
        result = estimate(asset)
        principal = float(result["input_summary"]["principal"])
        carrying_amount = float(result["input_summary"]["carrying_amount"])
        total_principal += principal
        total_carrying_amount += carrying_amount
        total_expected_loss += float(result["results"]["expected_loss_amount"])
        total_annual_income += float(result["results"]["gross_annual_income_rate"]) * carrying_amount
        weighted_wal += float(result["results"]["wal_years"]) * principal
        weighted_macaulay += float(result["results"]["macaulay_duration_years"]) * principal
        weighted_modified += float(result["results"]["modified_duration_years"]) * principal
        asset_results.append(
            {
                "asset_index": idx,
                "input_summary": result["input_summary"],
                "results": result["results"],
            }
        )

    wal_years = weighted_wal / total_principal if total_principal > 0 else 0.0
    macaulay_duration_years = weighted_macaulay / total_principal if total_principal > 0 else 0.0
    modified_duration_years = weighted_modified / total_principal if total_principal > 0 else 0.0
    expected_loss_ratio = total_expected_loss / total_carrying_amount if total_carrying_amount > 0 else 0.0
    annualized_expected_loss_rate = expected_loss_ratio / wal_years if wal_years > 0 else 0.0
    gross_annual_income_rate = total_annual_income / total_carrying_amount if total_carrying_amount > 0 else 0.0
    risk_adjusted_annual_return = gross_annual_income_rate - annualized_expected_loss_rate

    return {
        "pool_summary": {
            "asset_count": len(assets),
            "total_principal": round(total_principal, 6),
            "total_carrying_amount": round(total_carrying_amount, 6),
        },
        "results": {
            "wal_years": round(wal_years, 6),
            "macaulay_duration_years": round(macaulay_duration_years, 6),
            "modified_duration_years": round(modified_duration_years, 6),
            "expected_loss_amount": round(total_expected_loss, 6),
            "expected_loss_ratio": round(expected_loss_ratio, 8),
            "annualized_expected_loss_rate": round(annualized_expected_loss_rate, 8),
            "gross_annual_income_rate": round(gross_annual_income_rate, 8),
            "risk_adjusted_annual_return": round(risk_adjusted_annual_return, 8),
        },
        "asset_results": asset_results,
    }


def main():
    parser = argparse.ArgumentParser(description="Estimate duration and annualized risk for credit assets.")
    parser.add_argument("--input", required=True, help="Path to input JSON file.")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON output.")
    args = parser.parse_args()

    input_path = Path(args.input)
    with input_path.open("r", encoding="utf-8") as fh:
        payload = json.load(fh)

    result = aggregate_pool(payload["assets"]) if isinstance(payload, dict) and "assets" in payload else estimate(payload)
    if args.pretty:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
