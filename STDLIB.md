# Standard Library Substitution Reference

ZeroDep Doctor analyzes Python projects and recommends high-confidence, standard-library migrations to eliminate unnecessary third-party runtime dependencies.

---

## Package Killer — ZeroDep Graph Engine

### Problem Statement
Static dependency analyzers and refactoring tools often pull in heavy graph libraries like `networkx` to compute module dependency graphs, detect cycles, perform topological ordering, or find shortest paths between components. This introduces transitive runtime dependencies and setup overhead.

### Our Solution
ZeroDep Doctor includes a focused, high-performance graph engine built **100% using the Python Standard Library** (`collections.defaultdict`, `collections.deque`, `heapq`).

> **Note on Scope:** We do not claim to provide a 1:1 drop-in replacement for all of NetworkX (e.g. spectral graph theory, max-flow min-cut, or 3D layout rendering). Instead, we implemented a focused, NetworkX-inspired graph engine specifically tailored for software dependency graph traversal, cycle detection, hub module analysis, and topological sorting.

### Feature & Algorithm Matrix

| Capability | Algorithm / Implementation | Standard Library Used | Time Complexity | Test Coverage |
| :--- | :--- | :--- | :--- | :--- |
| **Graph Storage** | Adjacency lists (Directed Graph) | `collections.defaultdict(set)` | $O(V + E)$ space | 100% |
| **BFS Traversal** | Queue-based Breadth-First Search | `collections.deque` | $O(V + E)$ | Tested |
| **DFS Traversal** | Stack/Recursive Depth-First Search | Native recursion | $O(V + E)$ | Tested |
| **Cycle Detection** | 3-Coloring DFS (`WHITE`/`GRAY`/`BLACK`) | Recursion stack tracking | $O(V + E)$ | Tested |
| **Topological Sort** | Kahn's Algorithm (In-degree queue) | `collections.deque` | $O(V + E)$ | Tested |
| **Shortest Path** | Dijkstra's Algorithm | `heapq` Priority Queue | $O((V + E) \log V)$ | Tested |
| **Dependency Depth** | Dynamic Programming / DAG Memoization | Dictionary cache | $O(V + E)$ | Tested |
| **Hub Node Analysis** | In-degree ranking | `collections.Counter` | $O(V)$ | Tested |
| **Connected Components** | Disjoint-set / Union-Find | Dictionary parent pointers | $O(V \cdot \alpha(V))$ | Tested |

---

## Supported Migration Recipes

### 1. `requests` → `urllib.request` + `json`
* **Purpose:** HTTP request querying and JSON parsing.
* **Detected Usage:** `requests.get()`, `requests.post()`.
* **Confidence:** `HIGH` (downgrades to `LOW` if `requests.Session` or custom auth detected).
* **Limitations:** Complex OAuth session objects or custom transport adapters require manual migration.
* **Example Migration:**
  ```python
  # BEFORE (requests)
  import requests
  res = requests.get('https://api.example.com/data', timeout=5).json()

  # AFTER (stdlib)
  import urllib.request
  import json
  req = urllib.request.Request('https://api.example.com/data')
  with urllib.request.urlopen(req, timeout=5) as response:
      res = json.loads(response.read().decode('utf-8'))
  ```

---

### 2. `click` → `argparse`
* **Purpose:** CLI option and argument parsing.
* **Detected Usage:** `click.command()`, `@click.option`.
* **Confidence:** `HIGH`
* **Limitations:** Deeply nested subcommand trees require `argparse.add_subparsers()` boilerplate.
* **Example Migration:**
  ```python
  # BEFORE (click)
  import click
  @click.command()
  @click.option('--name', prompt='Your name')
  def hello(name):
      click.echo(f'Hello {name}')

  # AFTER (stdlib)
  import argparse
  parser = argparse.ArgumentParser()
  parser.add_argument('--name', required=True)
  args = parser.parse_args()
  print(f'Hello {args.name}')
  ```

---

### 3. `colorama` → ANSI Escape Sequences
* **Purpose:** Terminal colored text output.
* **Detected Usage:** `colorama.Fore`, `colorama.Style`.
* **Confidence:** `HIGH`
* **Limitations:** Legacy Windows CMD without ANSI enabled requires `os.system("")` or `ctypes`.
* **Example Migration:**
  ```python
  # BEFORE (colorama)
  from colorama import Fore, Style
  print(Fore.RED + 'Error' + Style.RESET_ALL)

  # AFTER (stdlib)
  RED = '\033[31m'
  RESET = '\033[0m'
  print(f'{RED}Error{RESET}')
  ```

---

### 4. `tabulate` → `string.format`
* **Purpose:** Formatting tabular text data into aligned ASCII tables.
* **Detected Usage:** `tabulate.tabulate()`.
* **Confidence:** `HIGH`
* **Limitations:** Multi-line cell text wrapping requires `textwrap.wrap()`.
* **Example Migration:**
  ```python
  # BEFORE (tabulate)
  from tabulate import tabulate
  print(tabulate([['Alice', 24], ['Bob', 19]], headers=['Name', 'Age']))

  # AFTER (stdlib)
  rows = [['Alice', 24], ['Bob', 19]]
  print(f'{"Name":<10} {"Age":<5}')
  for name, age in rows:
      print(f'{name:<10} {age:<5}')
  ```

---

### 5. `networkx` → `ZeroDep Graph Engine` (`collections`)
* **Purpose:** Graph data structures, node/edge analytics, and path algorithms.
* **Detected Usage:** `networkx.Graph()`.
* **Confidence:** `HIGH`
* **Limitations:** Advanced graph theory algorithms (e.g. max-flow, spectral centrality) are not included.
* **Example Migration:**
  ```python
  # BEFORE (networkx)
  import networkx as nx
  G = nx.Graph()
  G.add_edge('A', 'B')

  # AFTER (stdlib)
  from zerodep.graph_engine import ZeroDepGraph
  G = ZeroDepGraph()
  G.add_edge('A', 'B')
  ```

---

### 6. `radon` → `ast` Node Visitor
* **Purpose:** Cyclomatic complexity metrics.
* **Detected Usage:** `radon.complexity`.
* **Confidence:** `MEDIUM`
* **Limitations:** Does not compute Radon Maintainability Index (MI) metrics.
* **Example Migration:**
  ```python
  # BEFORE (radon)
  from radon.complexity import cc_visit
  results = cc_visit(code)

  # AFTER (stdlib)
  import ast
  tree = ast.parse(code)
  complexity = sum(1 for node in ast.walk(tree) if isinstance(node, (ast.If, ast.For, ast.While)))
  ```

---

### 7. `python-dotenv` → `os` / `pathlib`
* **Purpose:** Loading key-value environment variables from `.env` files.
* **Detected Usage:** `dotenv.load_dotenv()`.
* **Confidence:** `HIGH`
* **Limitations:** Does not expand nested shell variables (`$VAR`) automatically.
* **Example Migration:**
  ```python
  # BEFORE (python-dotenv)
  from dotenv import load_dotenv
  load_dotenv()

  # AFTER (stdlib)
  import os, pathlib
  env_path = pathlib.Path('.env')
  if env_path.exists():
      for line in env_path.read_text().splitlines():
          if line and not line.startswith('#') and '=' in line:
              k, v = line.split('=', 1)
              os.environ[k.strip()] = v.strip()
  ```

---

### 8. `loguru` → `logging`
* **Purpose:** Application logging and formatting.
* **Detected Usage:** `loguru.logger`.
* **Confidence:** `HIGH`
* **Limitations:** Requires `logging.basicConfig()` setup to match Loguru defaults.
* **Example Migration:**
  ```python
  # BEFORE (loguru)
  from loguru import logger
  logger.info('Started')

  # AFTER (stdlib)
  import logging
  logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
  logging.info('Started')
  ```

---

### 9. `tomli` → `tomllib`
* **Purpose:** TOML manifest parsing.
* **Detected Usage:** `tomli.loads()`.
* **Confidence:** `HIGH`
* **Limitations:** `tomllib` is read-only in Python standard library (Python 3.11+).
* **Example Migration:**
  ```python
  # BEFORE (tomli)
  import tomli
  config = tomli.loads(toml_str)

  # AFTER (stdlib)
  import tomllib
  config = tomllib.loads(toml_str)
  ```

---

### 10. `pytz` → `zoneinfo`
* **Purpose:** Timezone lookup and datetime localization.
* **Detected Usage:** `pytz.timezone()`.
* **Confidence:** `HIGH`
* **Limitations:** Windows environments require system timezone database availability.
* **Example Migration:**
  ```python
  # BEFORE (pytz)
  import pytz
  tz = pytz.timezone('UTC')

  # AFTER (stdlib)
  from zoneinfo import ZoneInfo
  tz = ZoneInfo('UTC')
  ```
