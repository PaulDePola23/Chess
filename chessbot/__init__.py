"""ChessBot: a small alpha-beta chess engine built on python-chess."""

__version__ = "0.1.0"

from .evaluation import evaluate  # noqa: E402
from .search import Searcher, SearchResult  # noqa: E402

__all__ = ["Searcher", "SearchResult", "evaluate", "__version__"]
