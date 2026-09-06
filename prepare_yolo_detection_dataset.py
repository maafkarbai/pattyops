"""Prepare a Roboflow YOLO export for Ultralytics detection training.

The source dataset is never modified. Bounding-box rows are preserved and
polygon rows are converted to their enclosing YOLO bounding boxes in a new
dataset directory.
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

import yaml


SPLITS = ("train", "valid", "test")


def format_number(value: float) -> str:
    return f"{value:.10f}".rstrip("0").rstrip(".")


def convert_annotation(line: str, source: Path, line_number: int) -> str:
    fields = line.split()
    if not fields:
        return ""

    try:
        class_id = int(fields[0])
        coordinates = [float(value) for value in fields[1:]]
    except ValueError as exc:
        raise ValueError(f"{source}:{line_number}: invalid numeric annotation") from exc

    if class_id < 0:
        raise ValueError(f"{source}:{line_number}: class ID must be non-negative")

    if len(fields) == 5:
        x_center, y_center, width, height = coordinates
    elif len(fields) >= 7 and len(fields) % 2 == 1:
        xs = coordinates[0::2]
        ys = coordinates[1::2]
        x_min, x_max = min(xs), max(xs)
        y_min, y_max = min(ys), max(ys)
        x_center = (x_min + x_max) / 2
        y_center = (y_min + y_max) / 2
        width = x_max - x_min
        height = y_max - y_min
    else:
        raise ValueError(
            f"{source}:{line_number}: expected a YOLO box or polygon, got {len(fields)} fields"
        )

    values = (x_center, y_center, width, height)
    if not all(0.0 <= value <= 1.0 for value in values):
        raise ValueError(f"{source}:{line_number}: coordinates must be normalized to 0..1")
    if width <= 0 or height <= 0:
        raise ValueError(f"{source}:{line_number}: box width and height must be positive")

    return " ".join([str(class_id), *(format_number(value) for value in values)])


def prepare_dataset(source_root: Path, output_root: Path) -> None:
    source_root = source_root.resolve()
    output_root = output_root.resolve()
    yaml_path = source_root / "data.yaml"

    if not yaml_path.is_file():
        raise FileNotFoundError(f"Dataset YAML not found: {yaml_path}")
    if output_root.exists():
        raise FileExistsError(
            f"Output already exists: {output_root}. Remove or rename it before running again."
        )

    metadata = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    names = metadata.get("names")
    if not names:
        raise ValueError(f"No class names found in {yaml_path}")

    counts: dict[str, int] = {}
    polygon_rows = 0
    box_rows = 0

    try:
        for split in SPLITS:
            source_images = source_root / split / "images"
            source_labels = source_root / split / "labels"
            if not source_images.is_dir() or not source_labels.is_dir():
                raise FileNotFoundError(f"Missing {split}/images or {split}/labels in {source_root}")

            target_images = output_root / split / "images"
            target_labels = output_root / split / "labels"
            target_images.mkdir(parents=True)
            target_labels.mkdir(parents=True)

            images = sorted(path for path in source_images.iterdir() if path.is_file())
            for image_path in images:
                label_path = source_labels / f"{image_path.stem}.txt"
                if not label_path.is_file():
                    raise FileNotFoundError(f"Missing label for {image_path.name}: {label_path}")

                converted: list[str] = []
                for line_number, line in enumerate(
                    label_path.read_text(encoding="utf-8-sig").splitlines(), start=1
                ):
                    if not line.strip():
                        continue
                    if len(line.split()) == 5:
                        box_rows += 1
                    else:
                        polygon_rows += 1
                    converted.append(convert_annotation(line, label_path, line_number))

                shutil.copy2(image_path, target_images / image_path.name)
                (target_labels / label_path.name).write_text(
                    "\n".join(converted) + ("\n" if converted else ""), encoding="utf-8"
                )

            counts[split] = len(images)

        prepared_metadata = dict(metadata)
        prepared_metadata["train"] = "train/images"
        prepared_metadata["val"] = "valid/images"
        prepared_metadata["test"] = "test/images"
        prepared_metadata["nc"] = len(names)
        prepared_metadata["names"] = names
        (output_root / "data.yaml").write_text(
            yaml.safe_dump(prepared_metadata, sort_keys=False, allow_unicode=True), encoding="utf-8"
        )
    except Exception:
        if output_root.exists():
            shutil.rmtree(output_root)
        raise

    print(f"Prepared dataset: {output_root}")
    print("Images: " + ", ".join(f"{split}={counts[split]}" for split in SPLITS))
    print(f"Annotations: boxes={box_rows}, polygons converted={polygon_rows}")
    print(f"Training YAML: {output_root / 'data.yaml'}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path, help="Roboflow dataset directory")
    parser.add_argument("output", type=Path, help="New detection dataset directory")
    args = parser.parse_args()
    prepare_dataset(args.source, args.output)


if __name__ == "__main__":
    main()
