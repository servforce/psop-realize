from __future__ import annotations

import argparse
import random
import shutil
from pathlib import Path


def read_classes(classes_path: Path) -> list[str]:
    classes = [line.strip() for line in classes_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not classes:
        raise ValueError(f"No classes found in {classes_path}")
    return classes


def pair_files(images_dir: Path, labels_dir: Path) -> list[tuple[Path, Path]]:
    pairs: list[tuple[Path, Path]] = []
    for image_path in sorted(images_dir.iterdir()):
        if not image_path.is_file():
            continue
        if image_path.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp", ".bmp"}:
            continue
        label_path = labels_dir / f"{image_path.stem}.txt"
        if not label_path.exists():
            raise FileNotFoundError(f"Missing label for image: {image_path.name}")
        pairs.append((image_path, label_path))

    if not pairs:
        raise ValueError(f"No image/label pairs found in {images_dir} and {labels_dir}")
    return pairs


def write_data_yaml(output_dir: Path, classes: list[str]) -> None:
    lines = [
        f"path: {output_dir.as_posix()}",
        "train: images/train",
        "val: images/val",
        f"nc: {len(classes)}",
        "names:",
    ]
    for index, class_name in enumerate(classes):
        lines.append(f"  {index}: {class_name}")
    (output_dir / "data.yaml").write_text("\n".join(lines) + "\n", encoding="utf-8")


def prepare_dataset(source_dir: Path, output_dir: Path, *, val_ratio: float, seed: int) -> dict[str, int]:
    images_dir = source_dir / "images"
    labels_dir = source_dir / "labels"
    classes_path = source_dir / "classes.txt"

    if not images_dir.is_dir():
        raise FileNotFoundError(f"Missing images directory: {images_dir}")
    if not labels_dir.is_dir():
        raise FileNotFoundError(f"Missing labels directory: {labels_dir}")
    if not classes_path.is_file():
        raise FileNotFoundError(f"Missing classes.txt: {classes_path}")

    classes = read_classes(classes_path)
    pairs = pair_files(images_dir, labels_dir)

    rng = random.Random(seed)
    rng.shuffle(pairs)
    val_count = max(1, int(round(len(pairs) * val_ratio)))
    if val_count >= len(pairs):
        val_count = max(1, len(pairs) - 1)
    train_pairs = pairs[:-val_count]
    val_pairs = pairs[-val_count:]

    for split in ("train", "val"):
        (output_dir / "images" / split).mkdir(parents=True, exist_ok=True)
        (output_dir / "labels" / split).mkdir(parents=True, exist_ok=True)

    def copy_pairs(items: list[tuple[Path, Path]], split: str) -> None:
        for image_path, label_path in items:
            shutil.copy2(image_path, output_dir / "images" / split / image_path.name)
            shutil.copy2(label_path, output_dir / "labels" / split / label_path.name)

    copy_pairs(train_pairs, "train")
    copy_pairs(val_pairs, "val")
    shutil.copy2(classes_path, output_dir / "classes.txt")
    notes_path = source_dir / "notes.json"
    if notes_path.exists():
        shutil.copy2(notes_path, output_dir / "notes.json")
    write_data_yaml(output_dir, classes)

    return {
        "classes": len(classes),
        "images_total": len(pairs),
        "images_train": len(train_pairs),
        "images_val": len(val_pairs),
    }


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Prepare a Label Studio YOLO export for Ultralytics training.")
    parser.add_argument(
        "--source",
        type=Path,
        default=Path(r"D:/daiyongna/download/dataset"),
        help="Label Studio export root containing images/, labels/, and classes.txt",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("datasets/robot_arm_parts"),
        help="Output dataset directory for Ultralytics",
    )
    parser.add_argument("--val-ratio", type=float, default=0.2, help="Validation split ratio")
    parser.add_argument("--seed", type=int, default=42, help="Shuffle seed")
    return parser


def main() -> None:
    args = build_arg_parser().parse_args()
    summary = prepare_dataset(args.source, args.output, val_ratio=args.val_ratio, seed=args.seed)
    print("Prepared dataset:")
    for key, value in summary.items():
        print(f"  {key}: {value}")
    print(f"  output: {args.output}")
    print(f"  yaml: {args.output / 'data.yaml'}")


if __name__ == "__main__":
    main()
