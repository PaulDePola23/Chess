import json

from chessbot.game_rating import POINTS, game_rating
from chessbot.server import config_js


def test_the_scale_rises_in_accuracy_and_rating():
    accuracies = [accuracy for accuracy, _ in POINTS]
    ratings = [rating for _, rating in POINTS]
    assert accuracies == sorted(accuracies) and len(set(accuracies)) == len(accuracies)
    assert ratings == sorted(ratings) and len(set(ratings)) == len(ratings)
    assert all(0 < accuracy < 100 for accuracy in accuracies)


def test_game_rating_reads_the_scale():
    (low_accuracy, low_rating), (next_accuracy, next_rating) = POINTS[0], POINTS[1]
    assert game_rating(0) == low_rating and game_rating(low_accuracy) == low_rating
    assert game_rating(100) == POINTS[-1][1]
    for accuracy, rating in POINTS:
        assert game_rating(accuracy) == round(rating / 50) * 50
    halfway = game_rating((low_accuracy + next_accuracy) / 2)
    assert low_rating <= halfway <= next_rating and halfway % 50 == 0
    samples = [game_rating(accuracy / 2) for accuracy in range(0, 201)]
    assert samples == sorted(samples)


def test_the_page_gets_the_scale():
    config = json.loads(config_js().split("=", 1)[1].strip().rstrip(";"))
    assert config["gameRating"] == [list(point) for point in POINTS]
