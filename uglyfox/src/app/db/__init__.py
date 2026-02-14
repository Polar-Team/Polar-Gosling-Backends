"""Database integration for UglyFox.

UglyFox uses the same database schemas as MotherGoose but with read-only
access for most operations. It queries runner state and metrics to make
pruning decisions.
"""
