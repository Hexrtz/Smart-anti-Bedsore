# node2/experiment: ข้อมูลเสียงและโมเดลจับเสียงไอ

โฟลเดอร์นี้เตรียมข้อมูลเสียงสำหรับเทรนโมเดล TinyML บน Node 2 (ESP32-S3 + INMP441)
คลาส: `cough` / `speech` / `background`

ทุกขั้นเป็นสคริปต์ใน `src/` ที่รันแยกกันได้ และผูกเข้าด้วยกันด้วย `Makefile`
ทุกคำสั่งรันจากโฟลเดอร์ `node2/experiment`

## ติดตั้ง

```bash
sudo apt install -y make ffmpeg
uv sync
```

ใส่ key ไว้ใน `node2/experiment/.env` (ไฟล์นี้อยู่ใน `.gitignore` ห้าม commit)

```
DATACOLLECTIVE_KEY=...     # Mozilla Data Collective สำหรับ Common Voice
```

Kaggle (สำหรับ COUGHVID) ใช้ `~/.kaggle/kaggle.json` (chmod 600) หรือ env `KAGGLE_API_TOKEN`

## ไพป์ไลน์

```
download -> check -> select -> interim -> process -> info
```

| ขั้น | คำสั่ง | สคริปต์ | อ่าน | เขียน |
| --- | --- | --- | --- | --- |
| 1. โหลดข้อมูล | `make download` | `src/download_data.py` | อินเทอร์เน็ต | `data/raw/<dataset>/`, `data/raw/SOURCES.md` |
| 2. ตรวจไฟล์เสียง | `make check` | `src/check_audio.py` | `data/raw/` | `data/reports/audio_info.csv` |
| 3. เลือกไฟล์ | `make select` | `src/select_data.py` | `data/raw/` + metadata, `audio_info.csv` | `data/manifest.csv` |
| 4. แปลงเป็น interim | `make interim` | `src/to_interim.py` | `data/manifest.csv` | `data/interim/` |
| 5. ตัด clip | `make process` | `src/make_clips.py` | `data/interim/` + `manifest.csv` | `data/processed/`, `data/clips.csv` |
| 6. สรุปปริมาณข้อมูล | `make info` | `src/dataset_info.py` | `data/<stage>/` + `manifest.csv` / `clips.csv` | หน้าจอ |
| 7. เทรน | `make train` | `src/train.py` + `configs/*.yaml` | `data/clips.csv`, `data/processed/` | `runs/<name>/<time>/` |
| 8. quantize | `make quantize` | `src/quantize.py` | `runs/<name>/<time>/model.pt` | `quant.json`, `model_int8.pt` ใน run เดียวกัน |
| 9. export TFLite Micro | `make export` | `src/export_tflite.py` | `runs/<name>/<time>/model.pt` | `<run>/tflite/` (.onnx, .tflite int8, `cough_model_data.h`, `cough_features.h`) |

`make` (ไม่ใส่ target) = `make process` ซึ่งรัน check, select, interim ให้ก่อนเสมอ
`make all` = download + process + info, `make help` แสดงคำสั่งทั้งหมด

ทุก target รันทุกครั้ง แต่แต่ละสคริปต์ข้ามงานที่ทำแล้ว จึงทำเฉพาะส่วนที่ขาด
อัดเสียง INMP441 เพิ่มใน `data/raw/inmp441/records/` แล้วสั่ง `make` ได้เลย

### 1. download (`src/download_data.py`)

| dataset | ขนาด | license | ใช้เป็น | โหลดอัตโนมัติ |
| --- | --- | --- | --- | --- |
| `coughvid` (Kaggle `nasrulhakim86/coughvid-wav`) | ~21 GB | CC BY 4.0 | cough | ใช่ |
| `speech_commands` v2 | 2.4 GB | CC BY 4.0 | speech, `_background_noise_` | ใช่ |
| `esc50` | ~600 MB | CC BY-NC 3.0 | background, hard negative (`coughing` ไปเป็น cough) | ใช่ |
| `common_voice_th` (Common Voice 27.0 ไทย) | 9 GB | CC0 1.0 | speech ภาษาไทย | สั่งเอง |
| `musan` | 11 GB | CC BY 4.0 | noise สำหรับ augmentation | สั่งเอง |
| `coswara` | ~16 GB | CC BY 4.0 | ไอ + หายใจ + นับเลข คนเดียวกัน | สั่งเอง |

```bash
make download                                  # ชุดที่โหลดอัตโนมัติ
make download DOWNLOAD="common_voice_th"       # เลือกเอง
uv run python src/download_data.py --list      # ดูทั้งหมด
```

- ชุดที่เสร็จแล้วมีไฟล์ `.done` รันซ้ำจะข้าม (`--force` เพื่อโหลดใหม่)
- โหลดลงไฟล์ `.part` ก่อน ถ้าหลุดกลางทางจะไม่ถูกนับว่าเสร็จ
- แตก tar ด้วย `filter="data"` และตรวจ path ใน zip กันไฟล์เขียนออกนอกโฟลเดอร์
- `data/raw/SOURCES.md` จดแหล่งที่มาและ license ไว้ใช้ในรายงาน
- `data/raw/inmp441/` คือเสียงที่ทีมอัดเองด้วย `node2/recorder/record_gui.py` ไม่ได้โหลดจากเน็ต

### 2. check (`src/check_audio.py`)

อ่านเฉพาะ header ของทุกไฟล์ (ใช้ทุก core, ~20 วินาทีสำหรับ 500k ไฟล์) รายงาน sample rate, channel, ความยาว
ถ้ามีไฟล์ต่ำกว่า 16 kHz จะ exit 1 และหยุดไพป์ไลน์ เพราะ upsample แล้วไม่ได้ความถี่ที่หายไปคืน

### 3. select (`src/select_data.py`)

กฎการเลือก

- **cough** = COUGHVID ทุกไฟล์ที่ `cough_detected >= COUGH_MIN` (default 0.8) + ESC-50 `coughing` + INMP441 ที่ label `cough`
- **other** = จำนวนไฟล์เท่ากับ cough ประกอบด้วย
  - INMP441 ที่ไม่ใช่ cough ทั้งหมด (ไมค์จริง ห้องจริง)
  - ESC-50 ที่ไม่ใช่ coughing ทั้งหมด
  - Speech Commands `_background_noise_` ทั้งหมด
  - ที่เหลือสุ่มจาก Speech Commands (คำพูด) และ Common Voice ไทย ตามสัดส่วน `CV_SHARE` (default 0.5)
- ตัดไฟล์ที่สั้นกว่า 1 s หรืออ่านไม่ได้ออก (ใช้ `audio_info.csv`)
- สุ่มด้วย `SEED` คงที่ (default 42) ได้ชุดเดิมทุกครั้ง
- **`HOLDOUT` (default `inmp441`)** dataset ที่ระบุไม่เข้า manifest เลย จึงไม่อยู่ใน train/val/test
  INMP441 คือไมค์จริงในห้องจริง จึงเก็บไว้เป็น **field test** แยก (notebook `test_inmp441.ipynb`) ใช้วัดว่าโมเดลที่เทรนจากข้อมูลอินเทอร์เน็ตใช้กับไมค์จริงได้แค่ไหน
  `make process HOLDOUT=` (ค่าว่าง) เอา INMP441 กลับเข้าเทรน ใช้เมื่ออัดได้หลายคนแล้ว (แบ่งบางคนเข้าเทรน ที่เหลือเก็บไว้ทดสอบ)

```bash
make select COUGH_MIN=0.9 CV_SHARE=0.7 SEED=1
make process HOLDOUT=                       # รวม INMP441 เข้าเทรนด้วย
```

`data/manifest.csv` หนึ่งแถวต่อหนึ่งไฟล์

| คอลัมน์ | ความหมาย |
| --- | --- |
| `interim_path` | path ใต้ `data/interim/` ที่ใช้ตอนเทรน |
| `raw_path` | path ใต้ `data/raw/` ต้นฉบับ |
| `dataset` | ชุดข้อมูลต้นทาง |
| `label` | `cough` / `speech` / `background` |
| `category` | คลาสเดิมในชุดข้อมูล (เช่น ESC-50 `dog`, คำใน Speech Commands) |
| `group` | id ของผู้พูดหรือการอัด ใช้แบ่ง train/val/test ไม่ให้คนเดียวกันอยู่ทั้งสองฝั่ง (INMP441 ตอนนี้มีผู้อัดคนเดียว จึงใช้ไฟล์ที่อัดเป็น group) |
| `seconds` | ความยาวไฟล์ |
| `cough_detected` | คะแนนจาก COUGHVID (ว่างสำหรับชุดอื่น) |

### 4. interim (`src/to_interim.py`)

แปลงไฟล์ใน manifest เป็น **mono, 16 kHz, PCM 16-bit WAV** แบบ 1 ต่อ 1 คงโครงโฟลเดอร์เดิม

- stereo ใช้ค่าเฉลี่ยของสองช่อง, downsample ด้วย `soxr` (HQ, มี anti-aliasing)
- ข้ามไฟล์ที่ output ใหม่กว่า input, เขียนลง `.tmp` แล้วค่อย rename
- `--prune` ลบไฟล์ใน `data/interim/` ที่ไม่อยู่ใน manifest แล้ว interim จึงตรงกับ manifest เสมอ
- ไฟล์ที่แปลงไม่ได้อยู่ใน `data/reports/to_interim_errors.csv`

ขั้นนี้ **ไม่ตัด clip, ไม่ใส่ label, ไม่คำนวณ log-mel**

- augmentation (ผสม noise, gain, เลื่อนเวลา) ต้องทำบน waveform ก่อนแปลงเป็น mel
- ค่า mel (`n_mels`, `n_fft`, `hop`) ยังเปลี่ยนได้ และต้องตรงกับเฟิร์มแวร์
- log-mel คำนวณบน GPU ตอนเทรนเร็วพอ

### 5. process (`src/make_clips.py`)

ตัด clip จาก interim ลง `data/processed/<split>/<label>/` และเขียน `data/clips.csv`

**ความยาว clip:** หน้าต่างเทรน 1 s (`CLIP_S`) + เสียงจริงเผื่อข้างละ 0.5 s (`MARGIN_S`) = ไฟล์ 2 s
ตอนเทรนให้ crop 1 s ออกมา: val/test crop ตรงกลางที่ `center_s`, train สุ่ม crop โดยให้ event ยังอยู่ในหน้าต่าง
ได้ time-shift augmentation โดยไม่ต้องเติมความเงียบ (`MARGIN_S=0` ถ้าต้องการ clip 1 s ตรงๆ)

**วิธีหาหน้าต่างของแต่ละ label**

| label | วิธี |
| --- | --- |
| cough | หาเสียงไอด้วย energy: คำนวณ RMS ทุก 10 ms (frame 25 ms), ใช้ threshold 2 ระดับ (hysteresis) ที่ 25% และ 50% ระหว่าง noise floor (percentile 10) กับ peak, เก็บช่วงยาว 0.08–1.5 s, จุดกลาง = จุดศูนย์ถ่วงพลังงานของช่วงนั้น แล้ววางไว้กลาง clip |
| | ไฟล์สาธารณะเก็บสูงสุด `MAX_PER_FILE` (3) เสียงไอต่อไฟล์ เลือกจากดังสุด จุดกลางห่างกัน ≥ 0.5 s, INMP441 เก็บทุกเสียงไอ |
| | ถ้า `labels.csv` ของ INMP441 มี `marks_sec` จะใช้ตำแหน่งนั้นแทน (ยึดกับ event ที่ตรวจเจอใกล้สุดในระยะ 0.3 s) |
| | ไฟล์ที่ peak สูงกว่า noise floor ไม่ถึง 12 dB ถือว่าไม่มีเสียงไอชัด ข้ามไป |
| speech | 1 s ที่ดังที่สุดของแต่ละไฟล์ (Speech Commands, Common Voice), INMP441 ใช้ทุก 1 s ที่ไม่เงียบ |
| background | ทุก 1 s ที่ไม่ซ้อนกันและไม่ใช่ความเงียบ digital (ESC-50 มีการเติมความเงียบท้ายไฟล์) |

ถ้า clip ล้นต้นหรือท้ายไฟล์ จะเลื่อนหน้าต่างเข้ามาในไฟล์แทนการเติมความเงียบ จุดไอจึงไม่ได้อยู่ที่ 1.0 s พอดีทุก clip ให้ใช้ `center_s` เสมอ

**สมดุล (นับเป็น clip):** non-cough = cough x `OTHER_RATIO` (1.0) โดย `BG_SHARE` (0.5) เป็น background ที่เหลือเป็น speech
ถ้า background ไม่พอ speech เติมให้, ถ้า non-cough รวมไม่พอ จะตัด cough ลง (ตัดเสียงไอลำดับ 2–3 ของไฟล์ก่อน) clip ของ INMP441 เก็บไว้ทั้งหมดเสมอ

**แบ่ง split:** train/val/test = 80/10/10 ตาม hash ของ `group` คนเดียวกันจึงไม่อยู่สอง split และเพิ่มข้อมูลใหม่แล้ว group เดิมไม่ย้าย split

```bash
make process MARGIN_S=0 MAX_PER_FILE=2 BG_SHARE=0.4
```

`data/clips.csv` (เวลาเป็นวินาที นับจากต้นไฟล์ clip)

| คอลัมน์ | ความหมาย |
| --- | --- |
| `clip_path` | path ใต้ `data/processed/` |
| `split` | `train` / `val` / `test` |
| `label`, `dataset`, `category`, `group`, `interim_path` | จาก manifest |
| `src_start_s` | clip เริ่มที่วินาทีเท่าไหร่ของไฟล์ interim (ติดลบ = เติมความเงียบด้านหน้า) |
| `center_s` | จุดกลางของหน้าต่าง 1 s (cough = จุดกลางเสียงไอ) |
| `event_start_s`, `event_end_s` | ช่วงเสียงไอ (label อื่น = หน้าต่าง 1 s) |
| `valid_start_s`, `valid_end_s` | ช่วงที่เป็นเสียงจริง ไม่ใช่ส่วนที่เติม |
| `peak_db` | ความดังสูงสุดของ event |

`data/processed/` สร้างใหม่ทั้งหมดทุกครั้งที่รัน (~25 วินาที)

### 6. info (`src/dataset_info.py`)

สรุปจำนวนไฟล์, ชั่วโมง, % ไฟล์, % ชั่วโมง, ความยาวเฉลี่ย แยกตาม dataset
`interim` จับคู่กับ `manifest.csv`, `processed` จับคู่กับ `clips.csv` จึงแยกตาม label และมีตาราง dataset x label (ชั่วโมง) ด้วย
`processed` มีตาราง label x split เพิ่ม ใช้ดูความสมดุลก่อนและหลังสุ่ม

```bash
make info                       # interim + processed
make info STAGE=raw
make info STAGE="raw interim processed"
```

## ตัวเลขล่าสุด (2026-11-12, `SEED=42`, ค่า default ทั้งหมด)

| stage | ไฟล์ | ชั่วโมง |
| --- | --- | --- |
| raw | 502,045 | 522.9 (Common Voice 428.6, COUGHVID 62.5, Speech Commands 29.0, ESC-50 2.8, INMP441 0.03) |
| interim | 30,332 | 47.6 |
| processed | 45,436 clip x 2 s | 25.2 |

| label | interim ไฟล์ | interim % ชั่วโมง | processed clip | processed % |
| --- | --- | --- | --- | --- |
| cough | 15,166 | 73.4 | 22,718 | 50.0 |
| speech | 13,200 | 20.6 | 13,287 | 29.2 |
| background | 1,966 | 6.0 | 9,431 | 20.8 |

| processed | train | val | test |
| --- | --- | --- | --- |
| cough | 18,187 | 2,259 | 2,272 |
| speech | 11,105 | 1,088 | 1,094 |
| background | 7,392 | 849 | 1,190 |

- interim สมดุลตามจำนวนไฟล์ แต่ไม่สมดุลตามชั่วโมง พอตัดเป็น clip แล้ว cough : other = 50 : 50
- ไฟล์ cough 510 จาก 15,166 ไม่เจอเสียงไอชัด จึงข้ามไป
- background ได้ 9,431 ซึ่งน้อยกว่าเป้า (11,359) speech จึงเติมส่วนที่ขาด
- ตรวจสุ่ม 500 clip เสียงไอ: ช่วงไอยาว median 0.75 s (เสียงไอที่ติดกันเป็นชุดถูกรวมเป็นช่วงเดียว), 85% มี `center_s` = 1.0 s พอดี, frame ที่ดังที่สุดอยู่ในช่วง 0.75–1.25 s 69%

## เทรนโมเดล (`src/train.py`)

```bash
make train                                   # configs/baseline_mlp.yaml
make train CONFIG=configs/cnn.yaml
make train CONFIG=configs/cnn.yaml SET="train.epochs=5 model.dropout=0.1"
uv run python src/train.py --config configs/cnn.yaml --set train.lr=0.001
```

`make train` ไม่รัน `process` ให้ ใช้ `data/processed` ที่มีอยู่ ถ้าเปลี่ยนข้อมูลให้รัน `make process` ก่อน

**config (`configs/*.yaml`)** ทุกค่าที่มีผลต่อผลลัพธ์อยู่ในไฟล์เดียว: data, features (log-mel), augment, model, train
แก้ค่าชั่วคราวด้วย `--set key=value` ได้โดยไม่ต้องแก้ไฟล์ ค่าที่ใช้จริง (หลัง `--set`) ถูกบันทึกลง `config.json` ของ run ทุกครั้ง

**ขั้นตอน**
- โหลด clip ทั้งหมดเข้า RAM (int16, ~3 GB, ~16 s)
- log-mel: `n_fft` 512, `win_length` 400 (25 ms), `hop_length` 160 (10 ms), 40 mel, 20–8000 Hz, `center=False` ได้ 40 x 97 ต่อ 1 s
- `taper: hamming` คูณหน้าต่าง 1 s ทั้งหน้าต่างด้วย Hamming ก่อน STFT ทั้งตอนเทรนและตอน inference (อยู่ใน `model.feat`) **เฟิร์มแวร์ต้องทำเหมือนกัน**
- normalise ต่อ mel bin ด้วย mean/std ของชุด train (เก็บใน model และใน `config.json`)
- augmentation (train เท่านั้น): สุ่มตำแหน่งหน้าต่าง 1 s ใน clip 2 s (เสียงไอยังอยู่ในหน้าต่าง), gain ±6 dB, ผสม background ที่ SNR 5–20 dB ครึ่งหนึ่งของ batch
- loss: cross-entropy ถ่วงน้ำหนักคลาสแบบ balanced, AdamW + one-cycle LR
- ทุก epoch แสดง train loss, val accuracy, macro F1, cough recall, cough F1 เก็บ epoch ที่ `select_metric` (default `macro_f1`) บน val ดีที่สุด แล้ววัดบน test

**ผลลัพธ์ใน `runs/<name>/<YYYYmmdd-HHMMSS>/`**

| ไฟล์ | มีอะไร |
| --- | --- |
| `model.pt` | `state_dict` + config + classes (ใช้กับ `coughml.infer.load_run`) |
| `config.json` | config ที่ใช้จริง, `--set` ที่ใส่, md5 ของ `clips.csv`, จำนวน clip ต่อ split, mean/std ของ log-mel, git commit, เวอร์ชัน Python/torch, GPU |
| `model.json` | ชนิดโมเดล, จำนวน parameter, ขนาด (float32 / int8), input shape, architecture |
| `metrics.json` | best epoch, val/test: accuracy, macro F1, cough recall/precision/F1, ค่าต่อคลาส, confusion matrix |
| `history.csv` | ค่าทุก epoch |

**ผลล่าสุด (2026-11-12, `HOLDOUT=inmp441`, test split)**

| run | params | int8 | accuracy | macro F1 | cough recall | cough F1 |
| --- | --- | --- | --- | --- | --- | --- |
| `baseline_mlp/20261112-174723` (MLP 256-128) | 1,027,587 | 1,004 KB | 0.940 | 0.937 | 0.945 | 0.949 |
| `cnn/20261112-175155` (CNN 16-32-64) | 23,603 | 23 KB | 0.974 | 0.973 | 0.981 | 0.977 |

ตัวเลขนี้วัดบน test split ซึ่งเป็นข้อมูลอินเทอร์เน็ตทั้งหมด ไม่ใช่ผลกับไมค์จริง
run ก่อนหน้าที่เทรนตอนยังไม่มี `HOLDOUT` (INMP441 speech อยู่ใน train, cough อยู่ใน val) ย้ายไปไว้ที่ `runs/_archive_with_inmp441/` notebook ไม่แสดง run ในโฟลเดอร์ที่ขึ้นต้นด้วย `_`
`config.json` ของทุก run มี `data.datasets` บอกว่าใช้ dataset ไหนเทรน

## Quantize int8 (`src/quantize.py`)

```bash
make quantize                                  # run ล่าสุดของทุก config
make quantize RUN=runs/cnn/20261112-175155
```

post-training static quantization ของ PyTorch (eager mode, engine `x86`): weight int8 ต่อ channel, activation int8 ต่อ tensor, calibrate ด้วยหน้าต่างจาก train 2,048 อัน
fuse Conv-BN-ReLU และ Linear-BN ก่อน quantize ส่วน log-mel ยังเป็น float (บน ESP32 คำนวณก่อนเข้าโมเดล)
ผลเขียนลง `quant.json` (float vs int8 บน val และ test, ลดลงกี่ percentage point, prediction agreement, ขนาด) และ `model_int8.pt` (TorchScript, input = log-mel ที่ normalise แล้ว)

| run (val) | float32 acc | int8 acc | ลดลง | macro F1 ลดลง | cough recall ลดลง | ทายตรงกัน | ขนาด state_dict |
| --- | --- | --- | --- | --- | --- | --- | --- |
| MLP | 0.9398 | 0.9386 | 0.12 pp | 0.14 pp | -0.18 pp | 99.6% | 4,022 KB -> 1,017 KB |
| CNN | 0.9715 | 0.9717 | -0.02 pp | -0.03 pp | 0.32 pp | 99.2% | 99.5 KB -> 33.6 KB |

int8 แทบไม่เสีย accuracy (ต่างกันไม่เกิน 0.5 pp) ตัวเลขนี้เป็นการประมาณ int8 ของ TFLite บนบอร์ด ค่าจริงต้องวัดอีกครั้งหลังแปลงเป็น TFLite
PyTorch แจ้งว่า `torch.ao.quantization` จะย้ายไป `torchao` ในอนาคต ตอนนี้ยังใช้ได้ สคริปต์ซ่อนคำเตือนนี้ไว้

## Export เป็น TensorFlow Lite Micro สำหรับ ESP32-S3 (`src/export_tflite.py`)

เลือก TFLite Micro เพราะเฟิร์มแวร์ Node 2 เป็น Arduino/PlatformIO (ESP-DL ต้องใช้ ESP-IDF)
โมเดล CNN เล็ก (~9.4M MAC ต่อหน้าต่าง) จึงคาดว่า inference ใช้เวลาระดับ ms เทียบกับ hop 500 ms ยังไม่ได้วัดบนบอร์ด

```bash
uv sync --group tflite                         # ครั้งแรก: onnx, onnx2tf, tensorflow, ai-edge-litert
make export                                    # run ล่าสุดของทุก config
make export RUN=runs/cnn/20261112-175155
```

ขั้นตอน: `model.pt` -> ONNX (opset 17, NCHW) -> onnx2tf (TF SavedModel, NHWC) -> TFLite full-integer int8 (calibrate ด้วยหน้าต่างจาก train 1,024 อัน) -> C array
export เฉพาะตัว classifier ส่วน log-mel ทำบนเฟิร์มแวร์ก่อนเข้าโมเดล
วัดผล int8 ด้วย TFLite reference kernels (ไม่ใช้ XNNPACK) ซึ่งใกล้กับ TFLite Micro ที่สุด และเช็กว่า float TFLite ทายตรงกับ PyTorch 100%

ผลอยู่ใน `runs/<name>/<time>/tflite/`

| ไฟล์ | มีอะไร |
| --- | --- |
| `model.onnx` | โมเดล float, input [1, 1, 40, 97] |
| `saved_model/` | ผลจาก onnx2tf + `.tflite` แบบ float32 |
| `cough_<name>_int8.tflite` | โมเดล int8: input int8 [1, 40, 97, 1], output int8 logits [1, 3] |
| `cough_model_data.h` | ไฟล์ `.tflite` เป็น C array (`alignas(16)`) ใส่ในเฟิร์มแวร์ได้เลย |
| `cough_features.h` | ค่าคงที่ log-mel (ขนาด, STFT window, mel filterbank [257][40], mean/std), scale/zero point ของ input/output, ชื่อคลาส, ops ที่ต้อง register และขั้นตอนคำนวณใน comment |
| `export.json` | PyTorch float vs TFLite int8 บน val และ test, ขนาด, ops |

**ผล (val, TFLite int8 reference kernels)**

| run | `.tflite` | float acc | int8 acc | acc ลดลง | macro F1 ลดลง | cough recall ลดลง | ทายตรงกัน | ops |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| CNN `cnn/20261112-175155` | 29.8 KB | 0.9715 | 0.9717 | -0.02 pp | -0.02 pp | -0.22 pp | 99.8% | CONV_2D, MAX_POOL_2D, MEAN, FULLY_CONNECTED |
| MLP `baseline_mlp/20261112-174723` | 1,015.5 KB | 0.9398 | 0.9398 | 0.00 pp | 0.01 pp | -0.13 pp | 99.8% | RESHAPE, FULLY_CONNECTED |

ใช้ CNN บนบอร์ด (MLP 1 MB ใหญ่เกินไป) input ของ CNN: int8, scale 0.0206789, zero point -53

**ข้อควรรู้สำหรับเฟิร์มแวร์**
- ใช้ TFLite Micro ของ Espressif (`esp-tflite-micro` ซึ่งมี ESP-NN kernel ที่ปรับให้ S3) register เฉพาะ ops ใน `cough_features.h` ด้วย `MicroMutableOpResolver<4>`
- log-mel บนบอร์ดต้องทำตามขั้นตอนใน comment ของ `cough_features.h` ทุกข้อ (Hamming ทั้งหน้าต่าง, STFT ไม่ pad, Hann 400 อยู่กลาง 512, HTK mel, log + 1e-6, normalise, quantize)
- ขนาด tensor arena ยังไม่ได้วัด ให้เริ่มที่ประมาณ 100 KB แล้วดู `arena_used_bytes()` หลัง `AllocateTensors()`
- ควรวัด latency ของ log-mel และ inference แยกกันด้วย `esp_timer_get_time()` เพื่อใช้ในรายงาน (E7)

## ทดสอบกับไมค์จริง (`src/notebooks/test_inmp441.ipynb`)

1. เลือก run (default: test macro F1 สูงสุด)
2. เลือกไฟล์ใน `data/raw/inmp441/records/` แล้วกำหนด `EXPECTED` (ทั้งไฟล์เป็นเสียงอะไร) หรือ `TRUTH` (ช่วงเวลาจริง `(start_s, end_s, label)`)
3. sliding window 1 s, hop 0.5 s (ซ้อนกัน 0.5 s) ทายทุกหน้าต่าง Hamming taper มาจาก config ของ run
4. ตารางผลต่อหน้าต่าง, accuracy/recall เทียบกับ truth, กราฟ waveform + log-mel + probability, ฟังหน้าต่างที่สนใจ

คอลัมน์ `used_in` บอกว่าไฟล์นั้นถูกใช้ใน train/val/test แล้วหรือยัง เมื่อใช้ `HOLDOUT=inmp441` ทุกไฟล์ต้องเป็น `-`

ผลคร่าวๆ ของ CNN `cnn/20261112-175155` กับไฟล์ INMP441 (ยังไม่มี `TRUTH` จึงนับแค่จำนวนหน้าต่าง): ไฟล์ไอ 55 หน้าต่าง ทายว่า cough 16; ไฟล์พูด 3 ไฟล์ รวม 194 หน้าต่าง ทายว่า speech 96, background 93, cough 5
ยังบอกไม่ได้ว่าถูกกี่ % จนกว่าจะใส่ช่วงเวลาจริง แต่ cough 5 หน้าต่างในไฟล์พูดคือ false alarm แน่นอน
ไฟล์เสียงไอทั้งไฟล์มีช่วงเงียบระหว่างไอ ถ้าตั้ง `EXPECTED = "cough"` ทั้งไฟล์ accuracy จะต่ำ (ลองแล้วได้ 0.26) ให้ใส่ `TRUTH` เป็นช่วงเวลาที่ไอจริง

## งานที่ยังเหลือ

- เขียน log-mel + Hamming บนเฟิร์มแวร์ตาม `cough_features.h` แล้วเทียบ log-mel ของหน้าต่างเดียวกันกับที่ PyTorch คำนวณ
- วัด latency และหน่วยความจำของ CNN บน ESP32-S3 จริง
- อัด background ด้วย INMP441 เพิ่ม: แอร์, พัดลม, ทีวี, คนคุยข้างเตียง, **มอเตอร์ JGY-370 ตอนยกพนักพิง**
- อัดเสียงไอด้วย INMP441 หลายคน และใส่ `marks_sec` ใน `labels.csv`
- test ควรมีเสียง INMP441 จากห้องจริง ตอนนี้มีผู้อัดคนเดียว 4 ไฟล์ split จึงขึ้นกับ hash

## โครงโฟลเดอร์

```
node2/experiment/
  Makefile
  pyproject.toml  uv.lock  .python-version
  .env                      # key (ไม่ commit)
  src/
    download_data.py  check_audio.py  select_data.py  to_interim.py  make_clips.py  dataset_info.py
    train.py
    coughml/                # โค้ดที่ใช้ร่วม: features, data, models, metrics, infer
    notebooks/              # main.ipynb (สำรวจข้อมูล), test_inmp441.ipynb (ทดสอบไมค์จริง)
  configs/                  # baseline_mlp.yaml, cnn.yaml
  runs/                     # ผลการเทรน (ไม่ commit)
  data/                     # ไม่ commit (ไฟล์ใหญ่ และมีเสียงคนจริง)
    raw/<dataset>/
    interim/<dataset>/      # mono 16 kHz
    processed/<split>/<label>/   # clip 2 s
    reports/                # audio_info.csv, to_interim_errors.csv
    manifest.csv            # ไฟล์ที่เลือก (select)
    clips.csv               # clip ที่ตัดแล้ว (process)
```

## ข้อควรระวัง

- `data/raw/inmp441/` เป็นเสียงคนจริง แชร์เฉพาะในทีม (Google Drive แบบ private) ห้ามเปิด public
- ไม่มี dataset สาธารณะของเสียงสำลัก เสียง choke ต้องอัดแบบจำลองเอง และระบุในรายงาน
