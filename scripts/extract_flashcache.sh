#!/bin/bash
###############################################################################
# extract_flashcache.sh  (root/dcli edition)
#
# Improved replacement for the manual exacli loop described in:
#   https://blogs.oracle.com/exadata/viewing-flash-cache-contents
#
# This version runs as root over the SSH equivalence that's already set up
# between the database node and the storage cells (standard on Exadata), via
# dcli, so there's no need to create a dedicated cell user/role first. It
# calls `cellcli` remotely instead of going through exacli's REST/cookie-jar
# session model.
#
# What's different from the blog version:
#   - No exacli, no role/user creation, no cookie jar - just root + dcli,
#     which is already trusted on every cell.
#   - dcli fans out to all cells itself (no hand-rolled backgrounding needed),
#     and -t sets a per-cell timeout so one dead cell doesn't hang the run.
#   - Per-cell failures are captured into a separate .err file instead of
#     silently mixing into the data.
#   - Adds a run timestamp column so multiple runs can be loaded into the
#     same table and trended over time.
#   - Emits clean CSV with a header, ready for flashcache_views.sql or
#     flashcache_dashboard.html - no sed/whitespace-collapsing needed.
#
# Usage:
#   ./extract_flashcache.sh -g /home/oracle/cell_group -o /tmp/fc_data
#
# Requires: dcli available on the db node (it ships with Exadata under
# /usr/local/bin), and root SSH equivalence to every cell in the group file
# (already configured on Exadata; test with:
#   dcli -g cell_group -l root "hostname"
# before running this if you're not sure).
###############################################################################
set -uo pipefail

CELLGROUP="/home/oracle/cell_group"
OUTDIR="/tmp/fc_data"
TIMEOUT_SECS=60

usage() {
  cat <<EOF
Usage: $0 [-g cell_group_file] [-o output_dir] [-t timeout_secs]

  -g cell_group_file   one cell hostname per line (default: /home/oracle/cell_group)
  -o output_dir        where CSV/err files land (default: /tmp/fc_data)
  -t timeout_secs      per-cell ssh timeout passed to dcli (default: 60)

Sanity check before running, if you're not sure root SSH equivalence is set up:
  dcli -g cell_group_file -l root "hostname"
EOF
  exit 1
}

while getopts "g:o:t:h" opt; do
  case "$opt" in
    g) CELLGROUP="$OPTARG" ;;
    o) OUTDIR="$OPTARG" ;;
    t) TIMEOUT_SECS="$OPTARG" ;;
    h) usage ;;
    *) usage ;;
  esac
done

[[ -f "$CELLGROUP" ]] || { echo "ERROR: cell group file not found: $CELLGROUP" >&2; exit 1; }
command -v dcli >/dev/null 2>&1 || { echo "ERROR: dcli not found in PATH (expected under /usr/local/bin on the db node)" >&2; exit 1; }

mkdir -p "$OUTDIR"
RUN_TS=$(date +"%Y-%m-%d %H:%M:%S")
RUN_EPOCH=$(date +%s)
DATAFILE="$OUTDIR/flashcache_${RUN_EPOCH}.csv"
ERRFILE="$OUTDIR/flashcache_${RUN_EPOCH}.err"
RAWFILE=$(mktemp)
trap 'rm -f "$RAWFILE"' EXIT

ATTRS="dbid,dbUniqueName,objectNumber,tablespaceNumber,cachedSize,cachedKeepSize,cachedWriteSize,columnarCacheSize,columnarKeepSize,hitCount,missCount"

echo "run_ts,cell_name,dbid,db_name,object_id,tablespace_id,cached_bytes,cached_keep_bytes,cached_write_bytes,columnar_cache_bytes,columnar_keep_bytes,hit_count,miss_count" > "$DATAFILE"
: > "$ERRFILE"

echo "Collecting flashcachecontent from cells in $CELLGROUP as root via dcli (timeout=${TIMEOUT_SECS}s)..."

# dcli prefixes every output line with "cellname: ", including error lines,
# which is exactly what lets us separate good rows from per-cell failures
# without any extra bookkeeping.
dcli -g "$CELLGROUP" -l root -t "$TIMEOUT_SECS" \
  "cellcli -e \"list flashcachecontent attributes ${ATTRS}\"" > "$RAWFILE" 2>&1

while IFS= read -r line; do
  cell="${line%%:*}"
  rest="${line#*: }"
  # Real data rows are space-separated attribute values, in the order requested.
  # Anything that doesn't parse into 11 fields is treated as an error/warning line.
  read -r -a fields <<< "$rest"
  if [[ ${#fields[@]} -ge 11 ]]; then
    printf '%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s\n' \
      "$RUN_TS" "$cell" "${fields[0]}" "${fields[1]}" "${fields[2]}" "${fields[3]}" \
      "${fields[4]}" "${fields[5]}" "${fields[6]}" "${fields[7]}" "${fields[8]}" \
      "${fields[9]}" "${fields[10]}" >> "$DATAFILE"
  else
    echo "$line" >> "$ERRFILE"
  fi
done < "$RAWFILE"

ROWS=$(($(wc -l < "$DATAFILE") - 1))
echo "Done. $ROWS flash cache entries written to $DATAFILE"

if [[ -s "$ERRFILE" ]]; then
  echo "WARNING: $(wc -l < "$ERRFILE") line(s) did not parse as data - see $ERRFILE" >&2
  echo "         (common causes: ssh timeout/unreachable cell, cellcli permission" >&2
  echo "         error, or a cell name containing ':' confusing the split above)" >&2
fi

if [[ $ROWS -eq 0 ]]; then
  echo "ERROR: no data collected at all. Sanity-check connectivity first:" >&2
  echo "  dcli -g $CELLGROUP -l root \"hostname\"" >&2
  exit 1
fi

ln -sf "$(basename "$DATAFILE")" "$OUTDIR/flashcache_latest.csv"
