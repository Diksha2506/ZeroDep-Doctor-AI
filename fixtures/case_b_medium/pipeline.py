"""Medium application — data pipeline with nested packages, optional deps, configuration."""

import json
import os
import csv
import pathlib
import logging
import datetime
import importlib
from typing import Optional, List, Dict, Any

# Third-party configuration
from dotenv import load_dotenv  # could be replaced with stdlib
import pytz                     # timezone handling — could use zoneinfo

# Optional heavy dependency (not always installed)
try:
    import pandas as pd
    HAS_PANDAS = True
except ImportError:
    pd = None
    HAS_PANDAS = False

# Load environment variables from .env file
load_dotenv()

LOG = logging.getLogger(__name__)
_TZ = pytz.timezone(os.environ.get("APP_TZ", "UTC"))


class DataSource:
    """Abstract base for data sources."""

    def read(self) -> List[Dict[str, Any]]:
        raise NotImplementedError


class CSVSource(DataSource):
    """Read data from CSV file using stdlib csv."""

    def __init__(self, filepath: str):
        self.path = pathlib.Path(filepath)

    def read(self) -> List[Dict[str, Any]]:
        if not self.path.exists():
            LOG.warning(f"CSV file not found: {self.path}")
            return []
        with self.path.open(newline="", encoding="utf-8") as fh:
            return list(csv.DictReader(fh))


class JSONSource(DataSource):
    """Read data from JSON file using stdlib json."""

    def __init__(self, filepath: str):
        self.path = pathlib.Path(filepath)

    def read(self) -> List[Dict[str, Any]]:
        if not self.path.exists():
            return []
        return json.loads(self.path.read_text(encoding="utf-8"))


class Pipeline:
    """Simple ETL pipeline."""

    def __init__(self, source: DataSource, output_dir: str):
        self.source = source
        self.output_dir = pathlib.Path(output_dir)

    def run(self) -> None:
        data = self.source.read()
        LOG.info(f"Read {len(data)} records")
        now = datetime.datetime.now(_TZ).isoformat()
        out = self.output_dir / f"output_{now}.json"
        self.output_dir.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(data, indent=2), encoding="utf-8")
        LOG.info(f"Wrote output: {out}")


def load_plugin(plugin_name: str) -> Optional[object]:
    """Dynamically load a data transformation plugin."""
    try:
        mod = importlib.import_module(f"plugins.{plugin_name}")
        return mod
    except ImportError:
        LOG.warning(f"Plugin not found: {plugin_name}")
        return None


def main():
    logging.basicConfig(level=logging.INFO)
    src_path = os.environ.get("DATA_SOURCE", "data.csv")

    if src_path.endswith(".json"):
        source = JSONSource(src_path)
    else:
        source = CSVSource(src_path)

    pipeline = Pipeline(source, output_dir="output")
    pipeline.run()


if __name__ == "__main__":
    main()
