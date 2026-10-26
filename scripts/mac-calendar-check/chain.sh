#!/bin/bash
# Every program up this shell's chain of parents, one per line: on a CI Mac
# one of them is what macOS asks about a step's use of the calendars.
pid=$$
while [ "$pid" -gt 1 ]; do
  exe=$(ps -o comm= -p "$pid")
  case "$exe" in
    /*) echo "$exe" ;;
    *) command -v "$exe" 2>/dev/null || true ;;
  esac
  pid=$(ps -o ppid= -p "$pid" | tr -d ' ')
done
