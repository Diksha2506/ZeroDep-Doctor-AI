"""Main entry point for bloated demo app showcasing various import styles and scope levels."""

import requests as req
from requests import get
import click
import tabulate
import networkx as nx
import importlib
import pandas as pd

# Star import demonstration
from math import *

# Note: pytz and colorama are declared in requirements/pyproject but NEVER imported or used here!

# Conditional import demonstration
try:
    import ujson as json
except ImportError:
    import json

# Scope-nested import demonstration
def outer_scope():
    def inner_scope():
        import math

def process_dataframe(data):
    # Essential dependency call site (pandas) -> no stdlib replacement
    df = pd.DataFrame(data)
    return df.describe()

def fetch_data(url: str):
    # Aliased call site: req.get
    res = req.get(url, timeout=5)
    return res.json()

def fetch_direct(url: str):
    # Direct from-import call site: get
    return get(url).json()

def complex_session_usage():
    # Complex requests call site (Session) -> low confidence for simple urllib auto-diff
    session = req.Session()
    session.auth = ('user', 'pass')

@click.command()
@click.option("--url", default="https://api.example.com")
def main_cli(url):
    print(f"Fetching from {url}")
    # Dynamic import call site
    mod = importlib.import_module("json")
    
    # NetworkX call site
    G = nx.Graph()
    G.add_edge("user", "api")

if __name__ == "__main__":
    main_cli()
