from pathlib import Path
import json
import pandas as pd

def natural_key(path):
    try:
        return (int(path.stem), path.name)
    except ValueError:
        return (10**9, path.name)

def iter_text_files(input_dir):
    return sorted(Path(input_dir).glob("*.txt"), key=natural_key)

def read_raw_text(path, encoding="utf-8-sig"):
    return Path(path).read_text(encoding=encoding)

def write_json(data, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

def write_csv(frame, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False, encoding="utf-8-sig")
