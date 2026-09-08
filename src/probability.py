"""
Converts NBM's published percentile forecast (P10, P25, P50, P75, P90) into
a probability that temperature exceeds a given threshold.

This is the piece that matters most. The blog post in this project's context
is explicit about why: a hand-rolled ensemble-spread-to-probability
conversion scored WORSE than a base-rate guess (Brier 0.2858 vs 0.2439).
The fix was using NOAA's own calibrated percentiles directly, with fat tails
beyond P10/P90 rather than assuming a plain normal distribution.

We do the same here: linear interpolation between published percentiles,
and a normal-tail extrapolation beyond P10/P90 with a sigma multiplier to
account for real temperature distributions being fatter-tailed than Gaussian.
"""
import math


def _std_normal_cdf(z: float) -> float:
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


def _std_normal_ppf(p: float) -> float:
    """
    Inverse standard normal CDF (quantile function) via a rational
    approximation (Acklam's algorithm). Good to ~1e-9, plenty for this use.
    """
    if not (0 < p < 1):
        raise ValueError("p must be strictly between 0 and 1")

    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00]

    p_low = 0.02425
    p_high = 1 - p_low

    if p < p_low:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
               ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    elif p <= p_high:
        q = p - 0.5
        r = q * q
        return (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q / \
               (((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1)
    else:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
                ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)


def probability_of_exceeding(
    percentiles: dict, threshold: float, sigma_multiplier: float = 1.15
) -> float:
    """
    percentiles: {"p10": float, "p25": float, "p50": float, "p75": float, "p90": float}
    threshold: the temperature the Kalshi contract strikes on
    sigma_multiplier: fat-tail adjustment applied beyond P10/P90

    Returns P(actual temperature >= threshold), in [0, 1].

    Method:
      - Between P10 and P90: piecewise-linear interpolation across the
        published percentile ladder. This trusts NOAA's calibration
        directly rather than assuming any particular distribution shape
        in the body of the distribution.
      - Beyond P10 or P90: extrapolate using a normal tail anchored at the
        nearest published percentile, with its implied local sigma scaled
        by sigma_multiplier to fatten the tail versus a naive Gaussian.
    """
    p10, p25, p50, p75, p90 = (
        percentiles["p10"], percentiles["p25"], percentiles["p50"],
        percentiles["p75"], percentiles["p90"],
    )

    ladder = [(0.10, p10), (0.25, p25), (0.50, p50), (0.75, p75), (0.90, p90)]

    if not all(ladder[i][1] <= ladder[i + 1][1] for i in range(len(ladder) - 1)):
        raise ValueError(f"Percentile ladder is not monotonic: {percentiles}")

    if p10 <= threshold <= p90:
        # Linear interpolation on the CDF between bracketing percentiles.
        for (q_lo, t_lo), (q_hi, t_hi) in zip(ladder, ladder[1:]):
            if t_lo <= threshold <= t_hi:
                if t_hi == t_lo:
                    cdf_at_threshold = q_lo
                else:
                    frac = (threshold - t_lo) / (t_hi - t_lo)
                    cdf_at_threshold = q_lo + frac * (q_hi - q_lo)
                return round(1 - cdf_at_threshold, 6)
        # Shouldn't reach here given the bounds check above.
        raise RuntimeError("Threshold fell inside [p10,p90] but no bracket matched")

    elif threshold < p10:
        # Extrapolate below P10 using a normal tail anchored at P10.
        z10 = _std_normal_ppf(0.10)  # negative
        local_sigma = abs((p50 - p10) / z10) if z10 != 0 else 1.0
        local_sigma *= sigma_multiplier
        z = (threshold - p10) / local_sigma + z10
        cdf_at_threshold = _std_normal_cdf(z)
        return round(1 - cdf_at_threshold, 6)

    else:  # threshold > p90
        z90 = _std_normal_ppf(0.90)  # positive
        local_sigma = abs((p90 - p50) / z90) if z90 != 0 else 1.0
        local_sigma *= sigma_multiplier
        z = (threshold - p90) / local_sigma + z90
        cdf_at_threshold = _std_normal_cdf(z)
        return round(1 - cdf_at_threshold, 6)


def probability_within_range(
    percentiles: dict, floor: float, cap: float, sigma_multiplier: float = 1.15
) -> float:
    """
    For Kalshi "between" markets (a temperature bucket, e.g. 78-80F), the
    contract pays out on P(floor <= temperature <= cap), not a simple
    exceeds/doesn't-exceed. This is P(exceed floor) - P(exceed cap).
    """
    if cap < floor:
        raise ValueError(f"cap ({cap}) must be >= floor ({floor})")
    p_exceed_floor = probability_of_exceeding(percentiles, floor, sigma_multiplier)
    p_exceed_cap = probability_of_exceeding(percentiles, cap, sigma_multiplier)
    return round(max(0.0, p_exceed_floor - p_exceed_cap), 6)
