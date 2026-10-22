"""Migration Engine with detailed stdlib replacement recipes, confidence levels, and limitations."""

from dataclasses import dataclass
from typing import Dict, List, Optional


@dataclass
class MigrationRecipe:
    package_name: str
    target_symbol: str
    stdlib_module: str
    confidence: str  # HIGH, MEDIUM, LOW
    why_replaceable: str
    limitations: str
    before_code: str
    after_code: str

# 10 High-Impact Stdlib Migration Recipes with complete metadata & usage awareness
RECIPES: Dict[str, MigrationRecipe] = {
    "requests": MigrationRecipe(
        package_name="requests",
        target_symbol="requests.get",
        stdlib_module="urllib.request",
        confidence="HIGH",
        why_replaceable="Simple HTTP GET request detected. Standard library urllib.request handles basic HTTP queries cleanly.",
        limitations="Complex OAuth sessions, requests.Session(), custom transport adapters, or persistent cookie jars require manual migration.",
        before_code="- import requests\n- res = requests.get('https://api.example.com/data', timeout=5).json()",
        after_code="+ import urllib.request\n+ import json\n+ req = urllib.request.Request('https://api.example.com/data')\n+ with urllib.request.urlopen(req, timeout=5) as response:\n+     res = json.loads(response.read().decode('utf-8'))"
    ),
    "click": MigrationRecipe(
        package_name="click",
        target_symbol="click.command",
        stdlib_module="argparse",
        confidence="HIGH",
        why_replaceable="CLI option parsing detected. Standard library argparse provides robust flag parsing and help formatting.",
        limitations="Complex nested subcommand trees and interactive prompts require additional argparse subparser boilerplate.",
        before_code="- import click\n- @click.command()\n- @click.option('--name', prompt='Your name')\n- def hello(name):\n-     click.echo(f'Hello {name}')",
        after_code="+ import argparse\n+ parser = argparse.ArgumentParser()\n+ parser.add_argument('--name', required=True)\n+ args = parser.parse_args()\n+ print(f'Hello {args.name}')"
    ),
    "colorama": MigrationRecipe(
        package_name="colorama",
        target_symbol="colorama.Fore",
        stdlib_module="ANSI Escape Codes",
        confidence="HIGH",
        why_replaceable="Terminal text styling detected. Standard ANSI escape codes work natively across modern terminals.",
        limitations="Legacy Windows CMD without ANSI support requires manual os.system('') or ctypes initialization.",
        before_code="- from colorama import Fore, Style\n- print(Fore.RED + 'Error' + Style.RESET_ALL)",
        after_code="+ RED = '\\033[31m'\n+ RESET = '\\033[0m'\n+ print(f'{RED}Error{RESET}')"
    ),
    "tabulate": MigrationRecipe(
        package_name="tabulate",
        target_symbol="tabulate.tabulate",
        stdlib_module="string.format",
        confidence="HIGH",
        why_replaceable="Basic table formatting detected. Python string column specifiers provide clean alignment without third-party deps.",
        limitations="Auto-wrapping complex multi-line cell content requires textwrap.wrap logic.",
        before_code="- from tabulate import tabulate\n- print(tabulate([['Alice', 24], ['Bob', 19]], headers=['Name', 'Age']))",
        after_code="+ rows = [['Alice', 24], ['Bob', 19]]\n+ print(f'{\"Name\":<10} {\"Age\":<5}')\n+ for name, age in rows:\n+     print(f'{name:<10} {age:<5}')"
    ),
    "networkx": MigrationRecipe(
        package_name="networkx",
        target_symbol="networkx.Graph",
        stdlib_module="ZeroDep Graph Engine (collections / heapq)",
        confidence="HIGH",
        why_replaceable="Graph data structures and path algorithms detected. ZeroDep's native Graph engine handles BFS, DFS, cycles, and Dijkstra.",
        limitations="Advanced graph theory algorithms (e.g. max-flow, spectral centrality) are not implemented.",
        before_code="- import networkx as nx\n- G = nx.Graph()\n- G.add_edge('A', 'B')",
        after_code="+ from zerodep.graph_engine import ZeroDepGraph\n+ G = ZeroDepGraph()\n+ G.add_edge('A', 'B')"
    ),
    "radon": MigrationRecipe(
        package_name="radon",
        target_symbol="radon.complexity",
        stdlib_module="ast",
        confidence="MEDIUM",
        why_replaceable="Cyclomatic complexity calculation detected. Standard ast NodeVisitor counts decision branches directly.",
        limitations="Does not compute advanced Radon Maintainability Index (MI) metrics out-of-the-box.",
        before_code="- from radon.complexity import cc_visit\n- results = cc_visit(code)",
        after_code="+ import ast\n+ tree = ast.parse(code)\n+ complexity = sum(1 for node in ast.walk(tree) if isinstance(node, (ast.If, ast.For, ast.While)))"
    ),
    "python-dotenv": MigrationRecipe(
        package_name="python-dotenv",
        target_symbol="dotenv.load_dotenv",
        stdlib_module="os / pathlib",
        confidence="HIGH",
        why_replaceable=".env environment file parsing detected. Standard library pathlib and string splitting handle key=value loading natively.",
        limitations="Does not support variable expansion ($VAR) or multiline strings without regex enhancement.",
        before_code="- from dotenv import load_dotenv\n- load_dotenv()",
        after_code="+ import os, pathlib\n+ env_path = pathlib.Path('.env')\n+ if env_path.exists():\n+     for line in env_path.read_text().splitlines():\n+         if line and not line.startswith('#') and '=' in line:\n+             k, v = line.split('=', 1)\n+             os.environ[k.strip()] = v.strip()"
    ),
    "loguru": MigrationRecipe(
        package_name="loguru",
        target_symbol="loguru.logger",
        stdlib_module="logging",
        confidence="HIGH",
        why_replaceable="Basic logger invocations detected. Standard library logging provides thread-safe formatting and file handlers.",
        limitations="Loguru's automatic context binding and colorized defaults require manual logging.basicConfig setup.",
        before_code="- from loguru import logger\n- logger.info('Started')",
        after_code="+ import logging\n+ logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')\n+ logging.info('Started')"
    ),
    "tomli": MigrationRecipe(
        package_name="tomli",
        target_symbol="tomli.loads",
        stdlib_module="tomllib",
        confidence="HIGH",
        why_replaceable="TOML parsing detected. Python 3.11+ includes built-in tomllib with identical API.",
        limitations="tomllib is read-only in stdlib (cannot write TOML).",
        before_code="- import tomli\n- config = tomli.loads(toml_str)",
        after_code="+ import tomllib\n+ config = tomllib.loads(toml_str)"
    ),
    "pytz": MigrationRecipe(
        package_name="pytz",
        target_symbol="pytz.timezone",
        stdlib_module="zoneinfo",
        confidence="HIGH",
        why_replaceable="Timezone lookup detected. Python 3.9+ standard library zoneinfo provides modern IANA timezone support.",
        limitations="Windows systems without system timezone databases may require tzdata if offline.",
        before_code="- import pytz\n- tz = pytz.timezone('UTC')",
        after_code="+ from zoneinfo import ZoneInfo\n+ tz = ZoneInfo('UTC')"
    ),
}

def get_recipe(package_name: str, used_symbols: Optional[Dict[str, int]] = None) -> Optional[MigrationRecipe]:
    recipe = RECIPES.get(package_name.lower())
    if not recipe:
        return None
    
    # Dynamic confidence downgrading for complex API usage
    if used_symbols and package_name.lower() == "requests":
        complex_calls = {"requests.Session", "requests.adapters", "requests.auth", "requests.cookies"}
        if any(sym in complex_calls for sym in used_symbols):
            return MigrationRecipe(
                package_name=recipe.package_name,
                target_symbol=recipe.target_symbol,
                stdlib_module=recipe.stdlib_module,
                confidence="LOW",
                why_replaceable="Complex requests usage (Session/Auth/Adapters) detected. Manual migration to urllib required.",
                limitations="Session persistence, transport adapters, or custom auth classes cannot be auto-diffed simply.",
                before_code=recipe.before_code,
                after_code=recipe.after_code,
            )
    return recipe
