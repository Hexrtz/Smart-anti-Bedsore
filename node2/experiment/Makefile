# Data pipeline for the cough model. Run from node2/experiment.
#
#   make            same as `make process`
#   make help       list targets and variables
#
# Order: download -> check -> select -> interim -> process
# Every step is incremental on its own (finished datasets have .done, converted
# files are skipped), so the targets always run and only do the missing work.
# New INMP441 recordings in data/raw/inmp441/ are picked up by `make interim`.

PY := uv run python

# datasets to download; empty = the default set in src/download_data.py
DOWNLOAD ?=
# select_data.py: min COUGHVID cough_detected, Common Voice share of the speech fill, seed
COUGH_MIN ?= 0.8
CV_SHARE ?= 0.5
SEED ?= 42
# make_clips.py: training window, extra audio each side, max coughs per public file,
# non-cough clips per cough clip, background share of non-cough
CLIP_S ?= 1.0
MARGIN_S ?= 0.5
MAX_PER_FILE ?= 3
OTHER_RATIO ?= 1.0
BG_SHARE ?= 0.5
# dataset_info.py: which stage(s) to summarise: raw interim processed
STAGE ?= interim processed

.DEFAULT_GOAL := process
.PHONY: help setup download check select interim process info all clean-interim clean-processed

help:
	@echo "make setup          uv sync (install packages)"
	@echo "make download       fetch datasets into data/raw   (DOWNLOAD=\"esc50 common_voice_th\")"
	@echo "make check          sample rate / channel report -> data/reports/audio_info.csv"
	@echo "make select         balanced file list -> data/manifest.csv   (COUGH_MIN=$(COUGH_MIN) CV_SHARE=$(CV_SHARE) SEED=$(SEED))"
	@echo "make interim        check + select, then convert manifest files to mono 16 kHz -> data/interim"
	@echo "make process        interim, then cut 1 s clips -> data/processed + data/clips.csv"
	@echo "                     (CLIP_S=$(CLIP_S) MARGIN_S=$(MARGIN_S) MAX_PER_FILE=$(MAX_PER_FILE) OTHER_RATIO=$(OTHER_RATIO) BG_SHARE=$(BG_SHARE))"
	@echo "make info           hours per dataset / label   (STAGE=\"$(STAGE)\")"
	@echo "make all            download + process + info"
	@echo "make clean-interim  delete data/interim (raw data is kept)"
	@echo "make clean-processed delete data/processed and data/clips.csv"

setup:
	uv sync

download:
	$(PY) src/download_data.py $(DOWNLOAD)

# exits non-zero when a file is below 16 kHz, which stops the pipeline
check:
	$(PY) src/check_audio.py --csv data/reports/audio_info.csv

select: check
	$(PY) src/select_data.py --cough-min $(COUGH_MIN) --cv-share $(CV_SHARE) --seed $(SEED)

# --prune removes interim files that are no longer in the manifest
interim: select
	$(PY) src/to_interim.py --manifest data/manifest.csv --prune

process: interim
	$(PY) src/make_clips.py --clip-s $(CLIP_S) --margin-s $(MARGIN_S) --max-per-file $(MAX_PER_FILE) \
	  --other-ratio $(OTHER_RATIO) --bg-share $(BG_SHARE) --seed $(SEED)

info:
	$(PY) src/dataset_info.py $(STAGE)

all: download process info

clean-interim:
	rm -rf data/interim

clean-processed:
	rm -rf data/processed data/clips.csv
