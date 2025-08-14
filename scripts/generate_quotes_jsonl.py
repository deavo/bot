#!/usr/bin/env python3
import argparse
import json
import uuid
from datetime import datetime
from pathlib import Path

DEFAULT_CATEGORIES = ["Любовь", "Жизнь", "Мотивация", "Дружба", "Юмор"]


def main():
    parser = argparse.ArgumentParser(description="Generate massive JSONL quotes dataset")
    parser.add_argument("--categories", nargs="*", default=DEFAULT_CATEGORIES, help="List of categories")
    parser.add_argument("--per-category", type=int, default=2000000, help="Quotes per category (default 2M x 5 = 10M)")
    parser.add_argument("--out", type=str, default="/app/backend/data/quotes_mega.jsonl", help="Output JSONL path")
    args = parser.parse_args()

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    total = 0
    with out_path.open("w", encoding="utf-8") as f:
        for cat in args.categories:
            for i in range(args.per_category):
                obj = {
                    "id": str(uuid.uuid4()),
                    "category": cat,
                    "text": f"Цитата №{i+1} о {cat}",
                    "author": "Генератор",
                    "source": "synthetic",
                    "created_at": datetime.utcnow().isoformat(),
                }
                f.write(json.dumps(obj, ensure_ascii=False) + "\n")
                total += 1
                if total % 100000 == 0:
                    print(f"Generated: {total}")

    print(f"Done. File: {out_path} Items: {total}")


if __name__ == "__main__":
    main()