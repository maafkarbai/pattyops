"""Train a versioned candidate and evaluate it without replacing the deployed model."""
from pathlib import Path
import argparse
import hashlib
import json
import traceback
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parent
RUN = ROOT / "runs/pattyops/bigger-v2-yolo26s"


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, default=str), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    if RUN.exists() and not args.resume:
        raise FileExistsError(f"Run already exists: {RUN}; use --resume to resume last.pt")
    RUN.mkdir(parents=True, exist_ok=True)
    import torch
    import ultralytics
    from ultralytics import YOLO

    def status(state, **extra):
        write_json(RUN / "status.json", dict(state=state, updated=datetime.now(timezone.utc).isoformat(), **extra))

    def epoch_done(trainer):
        status("training", epoch=trainer.epoch + 1, max_epochs=trainer.epochs,
               metrics=trainer.metrics, best_fitness=trainer.best_fitness)

    try:
        if not torch.cuda.is_available():
            raise RuntimeError("CUDA GPU required for this training configuration")
        data = ROOT / "datasets/pattyops-detect-v2/data.yaml"
        write_json(RUN / "environment.json", dict(torch=torch.__version__, ultralytics=ultralytics.__version__,
                   gpu=torch.cuda.get_device_name(0), data=str(data),
                   limitations=["Provided splits contain frames from the same source videos; metrics do not establish generalization to unseen kitchens.",
                                "SaltPepper: 5 training instances, 0 validation, 1 test; performance cannot be established.",
                                "Cheese-and-bun: 64 training instances, 5 validation, 5 test; evaluation is weak.",
                                "No automatic production deployment; camera-specific detection and tracking acceptance testing required."]))
        status("initializing")
        model = YOLO(str(RUN / "weights/last.pt") if args.resume else "yolo26s.pt")
        model.add_callback("on_fit_epoch_end", epoch_done)
        if args.resume:
            model.train(resume=True)
        else:
            model.train(data=str(data), project=str(RUN.parent), name=RUN.name, exist_ok=True,
                        epochs=200, patience=35, imgsz=640, batch=8, device=0, workers=4,
                        pretrained=True, optimizer="auto", seed=42, deterministic=True,
                        amp=True, cache=False, save=True, save_period=10, plots=True,
                        val=True, close_mosaic=15, cos_lr=True,
                        hsv_h=0.01, hsv_s=0.3, hsv_v=0.25)
        best = RUN / "weights/best.pt"
        status("evaluating", checkpoint=str(best))
        candidate = YOLO(str(best))
        metrics = candidate.val(data=str(data), split="test", imgsz=640, batch=8,
                                device=0, workers=4, plots=True,
                                project=str(RUN), name="held-out-test", exist_ok=True)
        write_json(RUN / "test_metrics.json", dict(aggregate=metrics.results_dict,
                   per_class=metrics.summary(), speed=metrics.speed,
                   checkpoint_sha256=hashlib.sha256(best.read_bytes()).hexdigest()))
        status("complete", checkpoint=str(best), metrics=metrics.results_dict,
               production_approved=False)
    except BaseException:
        status("failed", error=traceback.format_exc())
        raise


if __name__ == "__main__":
    main()
