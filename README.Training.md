# PattyOps larger-dataset training

Run `.venv-train\Scripts\python.exe train_pattyops_v2.py` from the repository root. The initial run is launched separately as a hidden background process; its logs are in `output/training-v2/`.

The candidate uses pretrained YOLO26s, 640-pixel images, batch size 8, CUDA device 0, seed 42, up to 200 epochs, and early stopping with patience 35. Color augmentation is reduced because cooking-state labels depend partly on appearance. These are baseline choices, not a production certification.

Artifacts are saved to `runs/pattyops/bigger-v2-yolo26s/`: `status.json` tracks progress, `results.csv` records epoch metrics, and `weights/best.pt` contains the candidate selected by validation performance. After training, the script evaluates the test split once and writes `test_metrics.json` and test plots. `environment.json` records library versions and limitations. `datasets/pattyops-detect-v2/audit.json` records the source archive hash and class counts.

To resume an interrupted training run with an existing `weights/last.pt`, use `.venv-train\Scripts\python.exe train_pattyops_v2.py --resume`. Do not start another process while training is active. Keep the computer awake and powered during the run.

The provided split has 1,010 training, 124 validation, and 122 test images. Source videos occur across splits, so evaluation may be optimistic. SaltPepper has only five training annotations, none in validation, and one in test; cheese-and-bun has 64/5/5 annotations. Collect more independent examples before relying on these classes.

Before deployment, evaluate footage from unseen kitchen sessions and cameras, including lighting changes, occlusion, crowded grills, and negative scenes. Define acceptable per-class precision/recall, counting error, tracking continuity, and device latency against operational requirements. Review the new class-name mapping. The existing deployed checkpoint is not replaced by this script.
