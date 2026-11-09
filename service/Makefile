UNAME_S := $(shell uname -s)
PYTHON ?= $(shell which python3)
CUR_DIR := $(shell pwd)
HOST := $(shell hostname -s)

ifeq ($(UNAME_S),Darwin)
# ---------------- macOS (launchd) ----------------
PLIST_NAME = com.daniel.window-tracker
PLIST_FILE = $(PLIST_NAME).plist
LAUNCH_AGENTS_DIR = $(HOME)/Library/LaunchAgents
DATA_DIR = $(HOME)/.local/share/window-tracker
HELPER_BIN = bin/mac_window_helper

.PHONY: all build install start stop restart status logs clean

all: build

build:
	@mkdir -p bin
	@if command -v swiftc >/dev/null 2>&1; then \
		echo "==> Compiling mac_window_helper..."; \
		swiftc -O mac_window_helper.swift -o $(HELPER_BIN); \
	else \
		echo "==> Warning: swiftc not found, will use AppleScript fallback."; \
	fi

install: build
	@mkdir -p $(LAUNCH_AGENTS_DIR)
	@mkdir -p $(DATA_DIR)
	@sed -e "s|__PYTHON__|$(PYTHON)|g" \
	     -e "s|__SCRIPT__|$(CUR_DIR)/window_tracker.py|g" \
	     -e "s|__DATA_DIR__|$(DATA_DIR)|g" \
	     -e "s|__HOST__|$(HOST)|g" \
	     $(PLIST_FILE) > $(LAUNCH_AGENTS_DIR)/$(PLIST_FILE)
	@-launchctl unload $(LAUNCH_AGENTS_DIR)/$(PLIST_FILE) 2>/dev/null || true
	@launchctl load -w $(LAUNCH_AGENTS_DIR)/$(PLIST_FILE)
	@echo "==> window-tracker installed and started under launchd ($(PLIST_NAME))."

start:
	@launchctl load -w $(LAUNCH_AGENTS_DIR)/$(PLIST_FILE)

stop:
	@-launchctl unload $(LAUNCH_AGENTS_DIR)/$(PLIST_FILE) 2>/dev/null || true

restart: stop start

status:
	@launchctl list | grep $(PLIST_NAME) || echo "Service not loaded"

logs:
	@tail -f -n 50 $(DATA_DIR)/tracker.log $(DATA_DIR)/tracker.err

clean: stop
	@rm -f $(LAUNCH_AGENTS_DIR)/$(PLIST_FILE)
	@rm -rf bin
	@echo "==> Uninstalled from launchd and cleaned build artifacts."

else
# ---------------- Linux (systemd --user) ----------------
SERVICE_FILE = window-tracker.service
USER_SYSTEMD_DIR = $(HOME)/.config/systemd/user

.PHONY: all build install start stop restart status logs clean

all:
	@echo "Linux build: no compilation required."

build:
	@echo "Linux build: no compilation required."

install:
	mkdir -p $(USER_SYSTEMD_DIR)
	cp $(SERVICE_FILE) $(USER_SYSTEMD_DIR)/$(SERVICE_FILE)
	systemctl --user daemon-reload
	systemctl --user enable --now $(SERVICE_FILE)
	@echo "==> window-tracker installed and started under systemd --user."

start:
	systemctl --user start $(SERVICE_FILE)

stop:
	systemctl --user stop $(SERVICE_FILE)

restart:
	systemctl --user restart $(SERVICE_FILE)

status:
	systemctl --user status $(SERVICE_FILE)

logs:
	journalctl --user -u $(SERVICE_FILE) -f -n 50

clean:
	-systemctl --user stop $(SERVICE_FILE)
	-systemctl --user disable $(SERVICE_FILE)
	rm -f $(USER_SYSTEMD_DIR)/$(SERVICE_FILE)
	systemctl --user daemon-reload

endif
