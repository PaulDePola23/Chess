"""A game's accuracy as a rating: the rating of players who average that accuracy.

The post-game review gives each move an accuracy (lichess's formula,
``webapi.move_accuracy``) and a game the average of its player's moves'.
``POINTS`` pairs ratings with the average game accuracy of lichess players at
that rating, as the website's own review scores them. It was measured by
``scripts/calibrate_game_rating.py`` (run on GitHub by
.github/workflows/game-rating.yml): about 5,000 players between 600 and 2600
in rated rapid and classical games from June 2025, around 250 per 100-point
band, with a smooth curve through the averages. Between two points a game's
rating is read off the straight line joining them.

The review's shallow search tells weak play apart well but strong play
hardly at all: lichess 600s average 85.6% and 2500s 92.8%, and above about
2200 the averages barely move, so the scale stops there. Below its first
point a game rates "under 600", and above its last "2200+" (the page shows
these; ``game_rating`` just stops at the ends).

It says how well one game went, not how strong the player is: a single
game's accuracy swings a lot (a lichess 1500's middle half of games spans
about 88% to 93%), so the rating does too. An average over many games is
steadier.
"""

from __future__ import annotations

# (average game accuracy, rating), rising in both.
POINTS = [
    (85.2, 600),
    (86.5, 800),
    (87.8, 1000),
    (88.9, 1200),
    (89.8, 1400),
    (90.7, 1600),
    (91.3, 1800),
    (91.9, 2000),
    (92.3, 2200),
]


def game_rating(accuracy: float) -> int:
    """The rating that goes with a game's accuracy, rounded to 50 and kept within the measured range."""
    if accuracy <= POINTS[0][0]:
        return POINTS[0][1]
    for (low_accuracy, low_rating), (high_accuracy, high_rating) in zip(POINTS, POINTS[1:], strict=False):
        if accuracy <= high_accuracy:
            share = (accuracy - low_accuracy) / (high_accuracy - low_accuracy)
            return round((low_rating + share * (high_rating - low_rating)) / 50) * 50
    return POINTS[-1][1]
