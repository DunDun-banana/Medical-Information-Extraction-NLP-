from pathlib import Path
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]

def load_config(path="configs/base.yaml"):
    p = Path(path)
    if not p.is_absolute():
        p = PROJECT_ROOT / p
    return yaml.safe_load(p.read_text(encoding="utf-8"))

def resolved_paths(config):
    result = {}
    for key, value in config["paths"].items():
        p = Path(value)
        result[key] = p if p.is_absolute() else PROJECT_ROOT / p
    return result
