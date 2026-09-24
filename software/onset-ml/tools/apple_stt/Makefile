# Apple SpeechAnalyzer CLI — on-device speech-to-text (macOS 26+, Apple silicon).
BIN = apple-stt
all: $(BIN)
$(BIN): main.swift
	swiftc -O -o $@ $<
clean:
	rm -f $(BIN)
.PHONY: all clean
