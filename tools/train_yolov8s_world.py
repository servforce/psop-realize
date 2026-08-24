from __future__ import annotations

import argparse
from pathlib import Path


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Train YOLOv8s-World on a custom detection dataset.")
    parser.add_argument("--model", type=str, default="yolov8s-world.pt", help="Base YOLO-World weights")
    parser.add_argument(
        "--data",
        type=Path,
        default=Path("datasets/robot_arm_parts/data.yaml"),
        help="Dataset YAML generated from Label Studio export",
    )
    parser.add_argument("--epochs", type=int, default=100, help="Training epochs")
    parser.add_argument("--imgsz", type=int, default=640, help="Training image size")
    parser.add_argument("--batch", type=int, default=4, help="Batch size")
    parser.add_argument("--device", type=str, default="0", help="Device index or 'cpu'")
    parser.add_argument("--project", type=Path, default=Path("runs/yolo_world"), help="Ultralytics project dir")
    parser.add_argument("--name", type=str, default="robot_arm_parts_v1", help="Run name")
    parser.add_argument("--workers", type=int, default=0, help="Dataloader workers. Use 0 on WSL if workers crash.")
    parser.add_argument("--no-amp", action="store_true", help="Disable AMP checks/training mixed precision")
    parser.add_argument("--exist-ok", action="store_true", help="Overwrite/reuse the same run directory name")
    return parser


def main() -> None:
    args = build_arg_parser().parse_args()
    try:
        from ultralytics import YOLOWorld
    except ImportError as exc:  # pragma: no cover - depends on local environment
        raise SystemExit("ultralytics is not installed in this environment") from exc

    if not args.data.exists():
        raise SystemExit(f"Dataset YAML not found: {args.data}")

    project = args.project
    if not project.is_absolute():
        project = Path.cwd() / project

    model = YOLOWorld(args.model)
    model.train(
        data=str(args.data),
        epochs=args.epochs,
        imgsz=args.imgsz,
        batch=args.batch,
        device=args.device,
        project=str(project),
        name=args.name,
        workers=args.workers,
        amp=not args.no_amp,
        exist_ok=args.exist_ok,
    )


if __name__ == "__main__":
    main()
