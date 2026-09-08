# psop-realize 数据集与模型训练工具

`tools/` 目录包含 psop-realize 的离线辅助工具。本文件重点说明如何构造待标注图片集、使用 Label Studio 完成目标检测标注、整理导出的 YOLO 数据集，以及训练 YOLOv8s-World 模型。

## 与项目整体目标的关系

psop-realize 面向 PSOP 交付两类核心产物：**素材解析结果**和**可在手机本地运行的微调多模态小模型**，详见 [项目说明](../README.md)。

本目录现有训练工具只覆盖目标检测数据集整理和 YOLO-World 微调，为设备视觉识别提供基础能力。这里生成的 `best.pt` 不等同于最终端侧多模态模型；当前脚本尚不支持将原始多模态素材、PSOP Skill 和 PSOP Execution Graph 联合用于训练，也不包含手机端模型适配与执行验证链路。

## 两个核心脚本

| 阶段 | 脚本 | 作用 |
| --- | --- | --- |
| 标注后数据处理 | `tools/prepare_yolo_world_dataset.py` | 读取已经解压的 Label Studio YOLO 图片导出目录，检查图片与标签是否成对，随机拆分训练集和验证集，并生成 Ultralytics 使用的 `data.yaml`。 |
| 模型训练 | `tools/train_yolov8s_world.py` | 使用 Ultralytics `YOLOWorld` 加载 `yolov8s-world.pt` 或指定的本地基础权重，在整理后的数据集上训练模型。 |

需要特别注意：

- 仓库目前**没有自动收集待标注图片的脚本**。待标注图片需要从业务视频、现场照片、截图或其他上游数据源中准备。
- `prepare_yolo_world_dataset.py` 是**标注完成后的整理脚本**，不负责标注，也不能直接读取或解压 `.zip`。
- 现有数据处理脚本划分的是**训练集 `train` 和验证集 `val`**，不会生成独立测试集 `test`。

## 完整流程概览

```text
收集原始业务图片
  -> 整理成待标注图片集
  -> 在 Label Studio 创建目标检测项目
  -> 为图片绘制矩形框并标注类别
  -> 导出包含图片的 YOLO .zip 包
  -> 手动解压导出包
  -> prepare_yolo_world_dataset.py 检查并拆分 train/val
  -> 生成 data.yaml
  -> train_yolov8s_world.py 训练 YOLOv8s-World
  -> 在验证集和真实业务图片上评估 best.pt
  -> 将确认可用的 best.pt 配置到 psop-realize
```

以下命令默认从 psop-realize 仓库根目录执行：

```bash
cd /opt/psop-realize
```

## 第一步：构造待标注图片集

先从真实业务数据中收集一批待标注图片，例如：

- 从安装、装配、接线、调试或维修视频中抽取的代表性画面；
- 现场拍摄的设备、零部件和工具图片；
- 不同背景、光照、距离、角度和遮挡条件下的图片；
- 不包含目标物体的负样本，用于降低误检率。

建议先放到独立目录中：

```text
/path/to/robot_arm_parts_to_label/
├── frame_000001.jpg
├── frame_000002.jpg
├── frame_000003.png
└── ...
```

准备图片时建议遵守以下规则：

1. 图片文件名必须唯一，尤其不要让不同扩展名的图片使用相同主文件名，例如同时存在 `001.jpg` 和 `001.png`。
2. 训练集应覆盖实际部署时可能遇到的尺寸、视角、光照、背景、遮挡和目标大小。
3. 避免大量几乎完全相同的连续帧，否则会造成数据重复，并可能让验证指标虚高。
4. 在开始标注前确定类别名称，标注过程中不要随意改名、合并或调整类别含义。
5. 如果要复现仓库现有机械臂模型，可使用以下类别作为参考：

```text
gripper_assembly
lower_arm_link
robot_arm_base
screwdriver
servo
upper_arm_link
wrist_joint
```

## 第二步：在 Label Studio 中标注

[Label Studio](https://github.com/HumanSignal/label-studio) 是一个开源数据标注平台。GitHub 地址是项目源码仓库，不是直接在线标注页面；需要先自行安装或启动 Label Studio，然后在浏览器中使用。

官方资料：

- [安装 Label Studio](https://labelstud.io/guide/install)
- [图像矩形框目标检测模板](https://labelstud.io/templates/image_bbox.html)
- [导出标注数据](https://labelstud.io/guide/export.html)

### 2.1 启动 Label Studio

可以按照官方文档使用 pip、Docker 或 Docker Compose。下面是官方 Docker 方式的简化示例：

```bash
mkdir -p label-studio-data
docker run -it --rm \
  -p 8080:8080 \
  -v "$PWD/label-studio-data:/label-studio/data" \
  heartexlabs/label-studio:latest
```

启动后访问：

```text
http://127.0.0.1:8080
```

生产环境应按照 Label Studio 官方文档配置持久化目录、文件权限、数据库和访问控制。

### 2.2 创建目标检测项目

1. 登录 Label Studio，新建项目。
2. 选择图像目标检测模板 **Object Detection with Bounding Boxes**。
3. 添加本次训练需要的全部类别，并确认类别拼写和含义。
4. 导入第一步准备的待标注图片。图片较多时可使用 Label Studio 的本地或对象存储接入方式。

YOLO 目标检测需要使用矩形框标注，因此项目配置应使用 Label Studio 的 `RectangleLabels`，不要把分类标签、画笔 mask 或视频跟踪标注误当成当前训练脚本所需的检测框数据。

### 2.3 标注和复核

对每张图片执行以下操作：

1. 为每个需要识别的目标实例绘制尽量贴合物体边缘的矩形框。
2. 为矩形框选择正确且唯一的类别。
3. 同一张图中出现多个目标时，应分别标注，不能只标最明显的一个。
4. 对没有任何目标的负样本也要完成并提交任务。
5. 标注完成后检查漏标、错标、过大框、过小框和类别不一致问题。

建议先统一标注规范，再开始大批量标注；重要数据集最好安排第二人复核。

## 第三步：从 Label Studio 导出 YOLO 图片包

在 Label Studio 项目的 Data Manager 中选择导出：

1. 点击 **Export**。
2. 选择包含原始图片的 **`YOLO with Images`** 格式，其内部格式名为 `YOLO_WITH_IMAGES`；有些沟通材料也会简称为 “YOLO Image”。
3. 不要选择只包含标注、不包含图片的普通 `YOLO` 导出项。
4. 下载生成的 `.zip` 文件。

导出前应确保所有需要使用的任务都已提交。Label Studio Community Edition 的常规导出默认以已有标注的任务为主，未完成任务可能不会进入最终训练数据。

解压后，脚本要求 `--source` 指向**直接包含**以下内容的那一级目录：

```text
label_studio_yolo_export/
├── images/
│   ├── frame_000001.jpg
│   └── ...
├── labels/
│   ├── frame_000001.txt
│   └── ...
├── classes.txt
└── notes.json              # 可选
```

其中：

- `images/` 中的图片必须直接放在该目录下，当前脚本不会递归搜索子目录。
- `labels/` 中每张图片都必须有一个同主文件名的 `.txt` 标签文件。
- 没有目标的负样本也需要对应的空 `.txt` 文件。
- `classes.txt` 每行一个类别，行号从 0 开始对应 YOLO 标签中的 class id。Label Studio 可能按类别名排序导出，因此必须以导出的 `classes.txt` 为准，不要根据界面显示顺序猜测 class id，也不要在导出后单独调整类别顺序。
- 单条 YOLO 检测标签通常为 `class_id x_center y_center width height`，坐标值应归一化到 0～1。

## 第四步：解压并检查导出包

数据处理脚本不能直接读取 `.zip`，需要先解压：

```bash
mkdir -p datasets/label_studio_export
unzip /path/to/label-studio-yolo-with-images.zip \
  -d datasets/label_studio_export
```

确认真正的数据根目录：

```bash
find datasets/label_studio_export -maxdepth 3 -type f | head -50
```

有些导出包解压后会多一层项目目录。后续 `--source` 必须指向直接包含 `images/`、`labels/` 和 `classes.txt` 的目录，而不是固定指向解压目标目录。

在运行脚本前至少检查：

```bash
test -d datasets/label_studio_export/images
test -d datasets/label_studio_export/labels
test -f datasets/label_studio_export/classes.txt
cat datasets/label_studio_export/classes.txt
```

如果实际数据多套了一层目录，请在下面命令中修改 `--source`。

## 第五步：处理并拆分数据集

### 5.1 数据处理脚本

数据处理脚本是：

```text
tools/prepare_yolo_world_dataset.py
```

运行示例：

```bash
.venv/bin/python tools/prepare_yolo_world_dataset.py \
  --source datasets/label_studio_export \
  --output datasets/robot_arm_parts \
  --val-ratio 0.2 \
  --seed 42
```

参数说明：

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `--source` | 开发环境中的 Windows 路径 | Label Studio 导出包解压后的数据根目录。为避免使用无效默认路径，实际运行时应始终显式传入。 |
| `--output` | `datasets/robot_arm_parts` | 整理后供 Ultralytics 使用的数据集目录。 |
| `--val-ratio` | `0.2` | 验证集占全部图片的比例，建议使用 `0 < val-ratio < 1`。 |
| `--seed` | `42` | 随机拆分种子。相同输入、比例和种子会得到可复现的拆分。 |

脚本执行的工作：

1. 检查 `images/`、`labels/` 和 `classes.txt` 是否存在。
2. 查找 `.jpg`、`.jpeg`、`.png`、`.webp`、`.bmp` 图片。
3. 要求每张图片都有对应的 `labels/<图片主文件名>.txt`。
4. 按文件名排序后，使用 `--seed` 随机打乱。
5. 按 `--val-ratio` 拆分训练集和验证集。
6. 复制图片和标签到新的目录结构。
7. 复制 `classes.txt`；如果存在则同时复制 `notes.json`。
8. 生成 Ultralytics 使用的 `data.yaml`。

输出结构：

```text
datasets/robot_arm_parts/
├── images/
│   ├── train/
│   └── val/
├── labels/
│   ├── train/
│   └── val/
├── classes.txt
├── notes.json              # 源目录存在时才生成
└── data.yaml
```

正常情况下至少应准备 2 张图片；实际训练数据应远多于此。脚本会保证验证集至少有 1 张，但输入只有 1 张时会导致训练集为空。

> **重要：**脚本不会清空 `--output`。重复处理同一个输出目录可能残留旧文件，甚至让同一张图片出现在不同拆分中。每次正式生成数据集时，请使用新的空目录，或先确认旧目录可以安全清理。

### 5.2 当前没有独立测试集

标准术语如下：

- `train`：参与梯度更新，用于训练模型；
- `val`：训练期间评估指标、选择超参数和挑选 `best.pt`；
- `test`：训练和调参完成后进行一次独立的最终评估。

当前 `prepare_yolo_world_dataset.py` 只生成 `train` 和 `val`，`data.yaml` 也没有 `test:` 配置。因此本仓库当前准确的流程是“拆分训练集和验证集，然后训练并验证”，不能把验证集描述成独立测试集。

如果项目正式要求 `train/val/test` 三份数据，需要先扩展数据处理脚本，或者提前保留一批完全不参与训练和调参的数据作为独立测试集。独立测试集不能再次混入 `train` 或 `val`。

### 5.3 脚本不负责的检查

数据处理脚本只检查图片与标签文件是否成对，不会检查：

- 标签每一列的数量和数据类型；
- class id 是否超出 `classes.txt` 范围；
- 坐标是否位于 0～1；
- 矩形框是否过大、过小或越界；
- 数据是否重复或存在训练集/验证集内容泄漏。

开始训练前应人工抽查标签，并确认 `data.yaml` 中的 `names` 与 `classes.txt` 一致。

## 第六步：训练 YOLOv8s-World

### 6.1 训练脚本

训练脚本是：

```text
tools/train_yolov8s_world.py
```

安装项目依赖：

```bash
.venv/bin/python -m pip install -r requirements.txt
```

如果要使用 GPU，训练前先确认当前 Python 环境能够访问 CUDA：

```bash
.venv/bin/python -c "import torch; print('torch:', torch.__version__); print('cuda:', torch.cuda.is_available()); print('device:', torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'CUDA unavailable')"
```

训练示例：

```bash
.venv/bin/python tools/train_yolov8s_world.py \
  --model yolov8s-world.pt \
  --data datasets/robot_arm_parts/data.yaml \
  --epochs 100 \
  --imgsz 640 \
  --batch 4 \
  --device 0 \
  --workers 0 \
  --project runs/yolo_world \
  --name robot_arm_parts_v2
```

首次使用 `yolov8s-world.pt` 时，Ultralytics 通常会联网下载基础权重。离线环境可以通过 `--model /path/to/yolov8s-world.pt` 指定已经下载好的本地文件。

参数说明：

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `--model` | `yolov8s-world.pt` | YOLOv8s-World 基础权重或本地模型路径。 |
| `--data` | `datasets/robot_arm_parts/data.yaml` | 数据处理脚本生成的数据集配置。 |
| `--epochs` | `100` | 训练轮数。 |
| `--imgsz` | `640` | 训练输入图片尺寸。 |
| `--batch` | `4` | batch size；显存不足时优先减小为 `2` 或 `1`。 |
| `--device` | `0` | 使用的 GPU 编号；仅使用 CPU 时传 `cpu`。 |
| `--project` | `runs/yolo_world` | Ultralytics 训练输出根目录。 |
| `--name` | `robot_arm_parts_v1` | 本次实验名称，同时决定输出子目录名。 |
| `--workers` | `0` | DataLoader worker 数量；WSL 或多进程加载不稳定时使用 `0`。 |
| `--no-amp` | 未启用 | 显式传入后关闭 AMP 混合精度；只有出现兼容问题时再使用。 |
| `--exist-ok` | 未启用 | 允许复用同名输出目录。正式训练一般不要添加，以免覆盖或混入旧实验。 |

训练过程中 Ultralytics 会使用 `val` 集进行验证。典型输出目录为：

```text
runs/yolo_world/robot_arm_parts_v2/
├── weights/
│   ├── best.pt
│   └── last.pt
├── args.yaml
├── results.csv
└── ...                     # 曲线、混淆矩阵等，具体文件随版本变化
```

- `best.pt`：验证指标最好的权重，部署时通常优先使用。
- `last.pt`：最后一个 epoch 的权重，主要用于继续训练或问题排查。

仓库已经存在 `runs/yolo_world/robot_arm_parts_v1/weights/best.pt`。新实验建议使用新的 `--name`，不要直接覆盖已经验证过的模型。

## 第七步：验证训练结果

训练完成后可再次在验证集上执行评估：

```bash
.venv/bin/python - <<'PY'
from ultralytics import YOLOWorld

model = YOLOWorld("runs/yolo_world/robot_arm_parts_v2/weights/best.pt")
metrics = model.val(
    data="datasets/robot_arm_parts/data.yaml",
    split="val",
    device="0",
    imgsz=640,
)
print("mAP50:", metrics.box.map50)
print("mAP50-95:", metrics.box.map)
PY
```

除验证指标外，还应使用没有参与训练和调参的真实业务图片或视频进行人工抽查，重点检查：

- 类别是否正确；
- 是否存在漏检和误检；
- 检测框是否贴合目标；
- 小目标、遮挡、低光和复杂背景下是否稳定；
- 新模型是否确实优于当前生产模型。

## 第八步：将模型用于 psop-realize

确认模型可用后，将 psop-realize 配置指向新的 `best.pt`：

```text
VIDEO_GRAPH_INDEX_FINETUNED_YOLO_WORLD_MODEL=runs/yolo_world/robot_arm_parts_v2/weights/best.pt
```

如果 psop-realize 运行在 Docker 中，容器内必须能够访问该文件，并且配置应使用容器内路径，例如：

```text
VIDEO_GRAPH_INDEX_FINETUNED_YOLO_WORLD_MODEL=/app/runs/yolo_world/robot_arm_parts_v2/weights/best.pt
```

当前 Dockerfile 只复制仓库约定位置下的现有模型。使用新的实验目录时，需要同步调整镜像复制规则或通过 volume 挂载模型文件，不能只修改宿主机路径。

## 常见问题

### `Missing images directory`、`Missing labels directory` 或 `Missing classes.txt`

`--source` 指错了目录。检查压缩包是否多套了一层目录，并将 `--source` 指到直接包含三者的那一级。

### `Missing label for image`

某张图片缺少同主文件名的 `.txt`。回到 Label Studio 检查是否漏标、漏提交或导出不完整。负样本也需要空标签文件。

### 导出包只有标签，没有图片

导出时选择了普通 `YOLO`。请改选包含图片的 `YOLO with Images`（`YOLO_WITH_IMAGES`）。

### GPU 显存不足

先减小 `--batch`，必要时减小 `--imgsz`。不要在没有定位原因时直接关闭 AMP。

### 训练目录自动增加序号

这是 Ultralytics 为避免覆盖旧实验的正常行为。优先为每次实验设置唯一的 `--name`，不要为了固定目录而随意添加 `--exist-ok`。

### 为什么没有 `test` 目录

当前数据处理脚本只实现了 `train/val` 拆分。训练时用 `train`，训练过程和模型选择用 `val`；如果需要正式独立测试集，必须额外保留或扩展脚本。

## 其他工具

- `qwen-image-wireframe/`：由视频线框图 API 调用的图片转线框图辅助工具，具体说明见 [`qwen-image-wireframe/README.md`](qwen-image-wireframe/README.md)。
