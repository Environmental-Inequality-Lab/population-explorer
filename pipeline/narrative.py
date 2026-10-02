"""Comparative claims, as tested functions over numbers.

Every "higher", "grew", "faster than" on a page is an assertion about data. The
published site computed those words inside the stats script and shipped the
words rather than the numbers, so nothing downstream could check them — and on
every county page the White population comparison pointed the wrong way.

Three rules, each of which corresponds to a bug that reached production:

  * Compare unrounded values. The old code compared a county share rounded to
    an integer against an unrounded national one, so a county at 21.6% rounded
    to 22 and was called "higher" than a national 21.9%.

  * Give every comparison a tolerance, so "about the same" is reachable. With
    exact comparison of an integer against a continuous value it essentially
    never fires, and two places a tenth of a point apart are described as
    different.

  * Compare like with like. A claim may only be made when both sides come from
    the same universe — the same denominator convention, the same measure, the
    same year. `Claim.of()` refuses otherwise rather than producing a sentence.
"""

from __future__ import annotations

from dataclasses import dataclass

from pipeline.figures import PlaceFigures, Shares

# Percentage points within which two shares are "about the same". One point is
# below what a reader can act on and above the noise in a small geography.
SHARE_TOLERANCE_PP = 1.0
# Relative tolerance for growth rates, in percentage points of annual rate.
RATE_TOLERANCE_PP = 0.2


class IncomparableError(ValueError):
    """The two sides do not describe the same thing, so no claim is possible."""


@dataclass(frozen=True)
class Claim:
    """A comparison, kept as numbers plus the word that describes them."""
    value: float
    reference: float
    word: str            # "higher" | "lower" | "about the same"
    difference: float    # value - reference, in the unit of both
    tolerance: float

    @property
    def is_similar(self) -> bool:
        # From the numbers, not the word: callers pass their own word for
        # "similar" ("at about the same rate", "held steady"), and matching on
        # "about the same" made every one of those read as a difference --
        # "at about the same rate than the state".
        return abs(self.difference) <= self.tolerance

    @classmethod
    def of(cls, value: float | None, reference: float | None, *,
           tolerance: float = SHARE_TOLERANCE_PP,
           higher: str = "higher", lower: str = "lower",
           similar: str = "about the same") -> Claim | None:
        if value is None or reference is None:
            return None
        diff = value - reference
        if abs(diff) <= tolerance:
            word = similar
        else:
            word = higher if diff > 0 else lower
        return cls(value, reference, word, diff, tolerance)


def compare_share(
    subject: Shares, reference: Shares, code: str, *,
    tolerance: float = SHARE_TOLERANCE_PP,
) -> Claim | None:
    """Compare one category's share between two places.

    Refuses when the two sides were computed over different universes. That is
    precisely the published bug: county race shares divided by a
    residual-inclusive denominator were compared against a national figure
    whose residual was a different size, and the comparison inverted.
    """
    if subject.universe != reference.universe:
        raise IncomparableError(
            f"cannot compare a share of {subject.universe!r} against a share of "
            f"{reference.universe!r} — the denominators are different populations"
        )
    if set(subject.order) != set(reference.order):
        raise IncomparableError(
            "the two share sets contain different categories: "
            f"{sorted(set(subject.order) ^ set(reference.order))}"
        )
    return Claim.of(subject.share(code), reference.share(code), tolerance=tolerance)


def compare_growth(
    subject: PlaceFigures, reference: PlaceFigures, start: int, end: int, *,
    tolerance: float = RATE_TOLERANCE_PP,
) -> Claim | None:
    """Compare annualised growth over identical spans.

    Both sides use one formula over the same years. The old pipeline ran three
    different growth conventions in a single script, one of which raised to
    1/GROWTH_YEARS over a span of GROWTH_YEARS+1 years.
    """
    if subject.population is None or reference.population is None:
        return None
    a, b = subject.population.cagr(start, end), reference.population.cagr(start, end)
    if a is None or b is None:
        return None
    return Claim.of(a * 100, b * 100, tolerance=tolerance,
                    higher="faster", lower="slower", similar="at about the same rate")


def describe_change(series, start: int, end: int, *, tolerance_pct: float = 1.0) -> Claim | None:
    """Whether a quantity grew, shrank, or held steady, as a percent change."""
    a, b = series.at(start), series.at(end)
    if a is None or b is None or a <= 0:
        return None
    pct = 100.0 * (b - a) / a
    return Claim.of(pct, 0.0, tolerance=tolerance_pct,
                    higher="grew", lower="shrank", similar="held steady")


def concentration(shares: Shares, code: str, *, even_pct: float = 10.0,
                  tolerance: float = SHARE_TOLERANCE_PP) -> Claim | None:
    """How a decile's share compares to an even spread across the distribution.

    This is the honest income claim: the share is measured, so its distance
    from 10% is a real fact about where residents fall. It replaces the
    published site's dollar figure, which was the local decile mix multiplied
    by national decile means and therefore said nothing about local earnings.
    """
    return Claim.of(shares.share(code), even_pct, tolerance=tolerance,
                    higher="over-represented", lower="under-represented",
                    similar="about evenly represented")


UPPER_HALF_TOLERANCE_PP = 3.0


def upper_half(shares: Shares, reference: Shares | None, *,
               tolerance: float = UPPER_HALF_TOLERANCE_PP) -> Claim | None:
    """Share of residents in national deciles 6-10, against the reference's.

    Within three points reads as "about the same": 52 to 48 is not a place
    concentrated in the upper half.
    """
    def top(sh: Shares) -> float | None:
        codes = [c for c in sh.order if c.isdigit() and c != "0"]
        if len(codes) < 10:
            return None
        return sum(sh.share(c) or 0 for c in codes[5:])
    a = top(shares)
    b = top(reference) if reference is not None else 50.0
    if a is None or b is None:
        return None
    return Claim.of(a, b, tolerance=tolerance)


def skew(shares: Shares) -> str | None:
    """Which half of the national distribution residents fall in, if either."""
    codes = [c for c in shares.order if c.isdigit() and c != "0"]
    if len(codes) < 10:
        return None
    top = sum(shares.share(c) or 0 for c in codes[5:])
    bottom = sum(shares.share(c) or 0 for c in codes[:5])
    if abs(top - bottom) <= 2 * SHARE_TOLERANCE_PP:
        return "spread fairly evenly across the national income distribution"
    return ("concentrated in the upper half of the national income distribution"
            if top > bottom else
            "concentrated in the lower half of the national income distribution")
