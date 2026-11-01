"""Contribution analysis using the logarithmic mean Divisia index (LMDI).

For a parent metric that is a product of factors, log changes add up exactly,
so contributions leave no residual and do not depend on calculation order.
Logs need positive values, so anything else gives an undefined decomposition
with a reason rather than a partial or estimated result.
"""

import math
from dataclasses import dataclass

IDENTITY_TOLERANCE = 1e-9
NO_NET_CHANGE = 1e-12


@dataclass(frozen=True)
class Factor:
    name: str
    prior: float | None
    current: float | None
    exponent: int = 1


@dataclass(frozen=True)
class Contribution:
    name: str
    prior: float
    current: float
    exponent: int
    log_effect: float
    contribution: float  # In the parent metric's units; these sum to its change.
    share: float | None  # Signed share of the net change; can exceed 100%.
    weight: float  # Share of all movement in either direction; always 0 to 1.


@dataclass(frozen=True)
class Decomposition:
    parent: str
    prior: float | None
    current: float | None
    contributions: tuple[Contribution, ...] = ()
    undefined_reason: str | None = None

    @property
    def defined(self):
        return self.undefined_reason is None


def log_mean(a, b):
    if math.isclose(a, b, rel_tol=1e-12):
        return a
    return (b - a) / math.log(b / a)


def _undefined_reason(parent, prior, current, factors):
    values = [(parent, prior), (parent, current)]
    for factor in factors:
        values += [(factor.name, factor.prior), (factor.name, factor.current)]

    for name, value in values:
        if value is None:
            return f"{name} is undefined in at least one period."
        if value <= 0:
            return f"{name} is zero in at least one period, so its log change is undefined."
    return None


def _product(factors, attribute, scale):
    return scale * math.prod(getattr(f, attribute) ** f.exponent for f in factors)


def decompose(parent, prior, current, factors, scale=1.0):
    """Split the change in parent, where parent = scale * product of factors."""
    reason = _undefined_reason(parent, prior, current, factors)
    if reason:
        return Decomposition(parent, prior, current, undefined_reason=reason)

    if not (
        math.isclose(prior, _product(factors, "prior", scale), rel_tol=IDENTITY_TOLERANCE)
        and math.isclose(current, _product(factors, "current", scale), rel_tol=IDENTITY_TOLERANCE)
    ):
        return Decomposition(
            parent,
            prior,
            current,
            undefined_reason=f"The factors do not multiply to {parent}, so the definitions are inconsistent.",
        )

    effects = [f.exponent * math.log(f.current / f.prior) for f in factors]
    net = math.log(current / prior)
    gross = sum(abs(effect) for effect in effects)
    mean = log_mean(prior, current)

    contributions = tuple(
        Contribution(
            name=factor.name,
            prior=factor.prior,
            current=factor.current,
            exponent=factor.exponent,
            log_effect=effect,
            contribution=mean * effect,
            share=None if abs(net) < NO_NET_CHANGE else effect / net,
            weight=0.0 if gross == 0 else abs(effect) / gross,
        )
        for factor, effect in zip(factors, effects)
    )
    return Decomposition(parent, prior, current, contributions)
