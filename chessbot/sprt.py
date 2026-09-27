"""Statistics for engine matches: Elo with error bars, LOS and the SPRT.

Games are played in pairs, each opening once with each colour, and a pair is
scored 0, 0.5, 1, 1.5 or 2 points for the engine under test (the
"pentanomial" model that fishtest and fastchess use). Pairing cancels most of
the luck of the openings, so the error bars are smaller than for single
games.

The SPRT (sequential probability ratio test) decides between two hypotheses
about the true Elo difference, H0: elo = elo0 and H1: elo = elo1, and stops as
soon as the evidence clearly favours one: a change that is really worth elo1
or more passes with probability 1 - beta, one worth elo0 or less fails with
probability 1 - alpha. The log-likelihood ratio uses the usual normal
approximation (as cutechess and fastchess do).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

PAIR_SCORES = (0.0, 0.25, 0.5, 0.75, 1.0)  # a pair's points, as a fraction of 2


def expected_score(elo: float) -> float:
    return 1 / (1 + 10 ** (-elo / 400))


def elo_from_score(score: float) -> float:
    score = min(max(score, 1e-6), 1 - 1e-6)
    return -400 * math.log10(1 / score - 1)


@dataclass
class MatchStats:
    """Results so far, as counts of pairs scoring 0, 0.5, 1, 1.5 and 2 points."""

    pairs: list[int] = field(default_factory=lambda: [0, 0, 0, 0, 0])
    wins: int = 0
    draws: int = 0
    losses: int = 0

    def add_pair(self, first: float, second: float) -> None:
        """Record a pair of games, each scored 1, 0.5 or 0 for the engine under test."""
        self.pairs[round((first + second) * 2)] += 1
        for score in (first, second):
            if score == 1:
                self.wins += 1
            elif score == 0:
                self.losses += 1
            else:
                self.draws += 1

    @property
    def games(self) -> int:
        return self.wins + self.draws + self.losses

    @property
    def pair_count(self) -> int:
        return sum(self.pairs)

    def mean_and_variance(self) -> tuple[float, float]:
        """Mean score per game, and the variance of a pair's (halved) score."""
        n = self.pair_count
        if not n:
            return 0.5, 0.0
        mean = sum(count * s for count, s in zip(self.pairs, PAIR_SCORES, strict=True)) / n
        variance = sum(count * (s - mean) ** 2 for count, s in zip(self.pairs, PAIR_SCORES, strict=True)) / n
        return mean, variance

    def elo(self) -> tuple[float, float]:
        """Elo difference and the half-width of its 95% confidence interval."""
        mean, variance = self.mean_and_variance()
        n = self.pair_count
        if not n:
            return 0.0, math.inf
        margin = 1.959964 * math.sqrt(variance / n)
        low, high = elo_from_score(mean - margin), elo_from_score(mean + margin)
        return elo_from_score(mean), (high - low) / 2

    def los(self) -> float:
        """Likelihood of superiority: the chance the engine under test is really the stronger."""
        mean, variance = self.mean_and_variance()
        n = self.pair_count
        if not n or variance == 0:
            return 0.5 if mean == 0.5 else float(mean > 0.5)
        return 0.5 * (1 + math.erf((mean - 0.5) / math.sqrt(2 * variance / n)))

    def llr(self, elo0: float, elo1: float) -> float:
        """Log-likelihood ratio of H1 (elo = elo1) against H0 (elo = elo0)."""
        mean, variance = self.mean_and_variance()
        n = self.pair_count
        if n < 2 or variance == 0:
            return 0.0
        s0, s1 = expected_score(elo0), expected_score(elo1)
        return n * (s1 - s0) * (2 * mean - s0 - s1) / (2 * variance)


def sprt_bounds(alpha: float = 0.05, beta: float = 0.05) -> tuple[float, float]:
    """The LLR at which the test accepts H0 (lower) or H1 (upper)."""
    return math.log(beta / (1 - alpha)), math.log((1 - beta) / alpha)
