"""eToro Public API connector (demo + real read/write).

Demo and real accounts are separated structurally: API keys are bound to one
environment and request paths include an explicit ``demo`` segment for paper
trading. Live write profiles require mandate enforcement via the live action gate.
"""
