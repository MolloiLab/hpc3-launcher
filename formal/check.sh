#!/usr/bin/env bash
# Model-check the SSH-config editing design with TLC.
#
#   1. SSHConfig.tla       -- the real design. Every invariant must hold.
#   2. SSHConfigNoLock.tla -- the same design with _SSH_CONFIG_LOCK removed.
#      TLC must FIND a NoLostSession violation. Without this second run the
#      first proves very little: an invariant that cannot fail is not a test.
#
# What this does and does not buy us: it verifies the DESIGN of the concurrent
# read-modify-write (no lost blocks, mutual exclusion, nothing unallocated ever
# written, every thread finishes). It does NOT verify the Python -- the model is
# a hand-written abstraction and can drift from vscode_helper.py. The Python is
# covered by tests/test_ssh_config.py; these are complementary, not redundant.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
JAR="${TLA_TOOLS_JAR:-$HERE/.tla/tla2tools.jar}"
TLC_URL="https://github.com/tlaplus/tlaplus/releases/latest/download/tla2tools.jar"

if [ ! -f "$JAR" ]; then
  echo "Fetching tla2tools.jar -> $JAR"
  mkdir -p "$(dirname "$JAR")"
  curl -sSL --fail -o "$JAR" "$TLC_URL"
fi

cd "$HERE"
rm -rf states

run_tlc() {  # run_tlc <module>; prints output, returns TLC's exit status
  java -XX:+UseParallelGC -cp "$JAR" tlc2.TLC -deadlock -cleanup "$1" 2>&1
}

echo "=== SSHConfig.tla (must hold) ==="
if out=$(run_tlc SSHConfig.tla); then
  echo "$out" | grep -E "states generated|No error has been found"
else
  echo "$out"
  echo "FAIL: SSHConfig.tla has a counterexample -- the design is wrong." >&2
  exit 1
fi

echo
echo "=== SSHConfigNoLock.tla (must fail: proves the invariants have teeth) ==="
if out=$(run_tlc SSHConfigNoLock.tla); then
  echo "$out"
  echo "FAIL: the lock-free mutant passed. NoLostSession is vacuous -- the" >&2
  echo "      model no longer describes the read-modify-write it claims to." >&2
  exit 1
fi
if ! echo "$out" | grep -q "Invariant NoLostSession is violated"; then
  echo "$out"
  echo "FAIL: the mutant failed, but not with the expected lost-update." >&2
  exit 1
fi
echo "$out" | grep -E "Invariant NoLostSession is violated"
echo "  (expected: two sessions read the same config; the second write drops"
echo "   the first session's block -- exactly the bug the lock prevents)"

echo
echo "Formal checks passed."
