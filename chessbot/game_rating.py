"""A game's accuracy as a rating: about the rating of players who play that accurately.

The post-game review gives each move an accuracy (lichess's formula,
``webapi.move_accuracy``) and a game the average of its moves'. ``POINTS``
pairs the average game accuracy of players of known strength with their
rating, measured by ``scripts/calibrate_game_rating.py`` with the website's own
review: PLACEHOLDER. Between two points a game's rating is read off the
straight line joining them.

It says how well one game went, not how strong the player is: a single
game's accuracy swings a lot, so the rating does too.
"""

from __future__ import annotations

# (average game accuracy, rating), rising in both.
POINTS = [(60.0, 200), (80.0, 800), (95.0, 2000)]


def game_rating(accuracy: float) -> int:
    """The rating that goes with a game's accuracy, rounded to 50 and kept within the measured range."""
    if accuracy <= POINTS[0][0]:
        return POINTS[0][1]
    for (low_accuracy, low_rating), (high_accuracy, high_rating) in zip(POINTS, POINTS[1:], strict=False):
        if accuracy <= high_accuracy:
            share = (accuracy - low_accuracy) / (high_accuracy - low_accuracy)
            return round((low_rating + share * (high_rating - low_rating)) / 50) * 50
    return POINTS[-1][1]
