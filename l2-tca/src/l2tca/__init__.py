"""l2-tca: L2 order book reconstruction and execution cost analysis.

Stage 1 scope: a single venue (Kraken public WebSocket v2, ``book`` channel) and a
single configurable instrument. The package is split so that the plumbing
(``feed``, ``io``, ``bench``) is complete and tested, while the analytical core
(``book``, ``signals``, ``tca``) is specified but deliberately left unimplemented.
"""

__version__ = "0.1.0"

__all__ = ["__version__"]
