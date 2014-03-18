# webpipe-lib.sh
#
# Not executable, but should be sourceable by /bin/sh.
#

# every script needs this
set -o nounset

# stdout is important, so provide something to log to stderr.
log() {
  echo 1>&2 "$@"
}

# failure to create tools
die() {
  log "$@"
  exit 1
}

# Extract base filename, without extension.  Useful determining the output path
# of converters.
#
# GNU basename doesn't seem to let you remove an arbitrary extension.
BasenameWithoutExt() {
  local path=$1
  #echo $(basename $path)
  #echo "$path -> ${path%%.*}"
  python -c '
import os,sys
base, _ = os.path.splitext(sys.argv[1])  # spam/eggs.c -> spam/eggs
print os.path.basename(base)             # spam/eggs -> eggs ' \
  $path
}
