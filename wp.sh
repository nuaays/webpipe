#!/bin/bash
#
# Copyright 2014 Google Inc. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be found
# in the LICENSE file or at https://developers.google.com/open-source/licenses/bsd

#
# User facing executables for webpipe.
#
# Usage:
#   ./wp.sh <function name>

set -o nounset
set -o pipefail

#
# Path stuff
#

# cross platform readlink -f
realpath() {
  local path=$0
  local ostype=${OSTYPE:-}
  # test if ostype begins with "darwin".  ignore stdout of expr.
  if expr $ostype : darwin >/dev/null; then
    python -S -c 'import os,sys; print os.path.realpath(sys.argv[1])' $path
  else
    readlink -f $path
  fi
}

# dereference symlinks in $0
readonly THIS_DIR=$(dirname $(realpath $0))

webpipe_dev=${WEBPIPE_DEV:-}
if test -z "$webpipe_dev"; then
  export PYTHONPATH=$THIS_DIR
fi

#
# Utilities
#

log() {
  echo 1>&2 "$@"
}

die() {
  log "$@"
  exit 1
}

readonly INPUT_DIR=~/webpipe/input
# is watched necessary anymore?  no reason most people can't shell out to nc.
# but it is still possible.
readonly WATCH_DIR=~/webpipe/watched

# 'nc' is required for wp show and in the R client.  Right now we check at 'wp
# init' time.
check-tools() {
  local err="'nc' not found.  On Ubuntu/Debian, run 'sudo apt-get install netcat'"
  which nc >/dev/null || die "$err"
}

#
# Public
#

# Set up the default dir to watch.
init() {
  # Where files from the input dirs are moved and renamed to, so they are HTML
  # and shell safe.
  mkdir --verbose -p \
    ~/webpipe/renamed \
    $INPUT_DIR/sink \
    $WATCH_DIR \
    ~/webpipe/plugins
  # Where user can install their own plugins

  # Named pipe that receives paths relative to the sink dir.  We remove and
  # create the pipe to reset it?
  # NOTE: We want at least two ways of showing files:
  # - put something in the 'watched' dir
  # - wp show <filename>
  #
  # A named pipe can't handle both.  You would need a Unix socket.

  #rm --verbose ~/webpipe/input
  #mkfifo ~/webpipe/input
  #local exit_code=$?
  #if test $exit_code -eq 0; then
  #  log "Created ~/webpipe/input"
  #else
  #  log "mkfifo error"
  #fi

  # Do this last, since it dies.
  check-tools

  log "wp: init done"
}

# People can run print-events | xrender to directly to render on a different
# host.  For now we keep them separate, so we have an explicit and flexible
# pipeline.

print-events() {
  local input_dir=${1:-$WATCH_DIR}

  # --quiet: only print events
  # --monitor: loop forever
  # --format %f: print out the filename in the directory we're watching

  # close_write: when a file is closed after writing
  # create: creating a symlink (ln -sf of a dir alway does DELETE then CREATE)

  log "wp: Watching $input_dir"
  inotifywait --monitor --quiet -e close_write,create $input_dir --format '%f'
}

# render files to HTML.
xrender() {
  $THIS_DIR/webpipe/xrender.py "$@"
}

# serve HTML and static files.
serve() {
  $THIS_DIR/webpipe/serve.py "$@"
}

# Better/more portable server than nc.  Should only be used when we don't care
# about running on Mac.
socat-listen() {
  local port=$1
  # -u: unidirection (</dev/null would be the same)
  # fork: fork a child process for each connection; gives us the "loop"
  # behavior.
  socat -u TCP4-LISTEN:$port,fork -
}

# Run the whole pipeline.
#
# TODO:
# - Add flags that are common: --user-dir, --port (for server), etc.
# - What about rendering flags?
#
# $ webpipe run --port 8888

run() {
  local sessionName=${1:-}

  local stamp=$(date +%Y-%m-%d)
  if test -z "$sessionName"; then
    sessionName=$stamp
  else
    # Special syntax: + prepends the current date.
    # run +foo  ==>  session name is 2014-03-30-foo
    sessionName=$(echo $sessionName | sed s/+/$stamp-/)
  fi

  local session=~/webpipe/s/$sessionName
  mkdir -p $session

  export PYTHONUNBUFFERED=1

  # TODO: Add xrender --listen-port 8988.  If port is specified, then instead of
  # reading from stdin, we listen on a port.
  #
  # nc servers don't reliably work the same way on all machines.  socat isn't
  # installed.
  #
  # We might want recv --listen-port too, but maybe later.  And even server
  # --listen-port.
  xrender -p 8988 $INPUT_DIR $session \
    | serve serve $session
}

# Like run, but just test latency.
noop() {
  local sessionName=${1:-}

  local stamp=$(date +%Y-%m-%d)
  if test -z "$sessionName"; then
    sessionName=$stamp
  else
    # Special syntax: + prepends the current date.
    # run +foo  ==>  session name is 2014-03-30-foo
    sessionName=$(echo $sessionName | sed s/+/$stamp-/)
  fi

  export PYTHONUNBUFFERED=1

  # make sure it succeeds
  echo foo >/tmp/foo.txt
  echo foo.txt \
    | xrender /tmp /tmp \
    | serve noop
}

# NOTE: Use nc for a CLIENT only.  Anything else isn't portable.
nc-send() {
  local port=$1
  nc localhost $port
}

# Show a file, specifying file type first.
#
# $ wp show-as txt NOTES
# $ gen-html | wp show-as html
#
# wp as could be an alias.
show-as() {
  local ext=$1
  shift

  # no args; read from stdin
  if test $# -eq 0; then
    if test -z "$ext"; then
      ext=txt
    fi
    local tempfile=$INPUT_DIR/sink/$$.$ext
    cat > $tempfile
    echo $tempfile | nc-send 8988
  fi

  # TODO: respect $ext here.  Need to send it as a TNET message I suppose.

  for filename in "$@"; do
    if test ${filename:0:1} = /; then
      echo "$filename" | nc-send 8988
    else
      # relative path, make it absolute.
      echo "$PWD/$filename" | nc-send 8988
    fi
  done
}

as() {
  show-as "$@"
}

# Show a file (webpipe client).
# If no filename is given, then it reads from stdin.  File type is inferred
# from extension.
#
# $ wp show foo.csv
# $ ls -l | wp show

show() {
  show-as '' "$@"
}

publish() {
  $THIS_DIR/webpipe/publish.py "$@"
}

# set up reverse tunnel for receiving files.
wp-ssh() {
  log "webpipe: Setting up SSH reverse tunnel from remote port 8987 to localhost port 8987."
  ssh -R 8987:localhost:8987 "$@"
}

# Other actions:
# - sink (move from the stub?)
# - show <files...>
# - watch -- start the inotify daemon on watched
#
# Individual actions (for advanced users):
# - xrender
# - serve

help() {
  local topic=${1:-}
  case "$topic" in
    advanced)
      cat $THIS_DIR/doc/wp-help-advanced.txt
      ;;
    *)
      cat $THIS_DIR/doc/wp-help.txt
      ;;
  esac
}

recv() {
  export PYTHONUNBUFFERED=1
  $THIS_DIR/webpipe/recv.py "$@"
}

# TODO: This should be documented.  This is in conjunction with wp ssh, and
# remove wp-stub.sh send, I think.
# NOTE: We're using socat here because this is for Linux people?  Could also
# add recv -p (factor the code out of xrender and put it in util/common).
run-recv() {
  socat-listen 8987 \
    | recv ~/webpipe/input \
    | while read line; do echo $line | nc-send 8988; done
}

#
# Introspection
#

# So people can do scp $(webpipe stub-path) user@example.org:bin
stub-path() {
  local path=$THIS_DIR/wp-stub.sh
  if test -f $path; then
    echo $path
  else
    die "Invalid installation; $path doesn't exist"
  fi
}

scp-stub() {
  local path=$(stub-path)
  scp $path "$@"
}

version() {
  # TODO: Show the actual version?  For now just show the package-dir.
  # assuming that has the version.
  package-dir
}

# Use this to find stub path?
# TODO: Should there also be a user-dir thing?  I think that should always be
# ~/webpipe.
package-dir() {
  echo $THIS_DIR
}

if test $# -eq 0; then
  help
  exit 0
fi

case $1 in 
  # generally public ones
  help|init|run|noop|run-recv|package-dir|publish|show|show-as|as|stub-path|scp-stub|version)
    "$@"
    ;;
  ssh)
    # need to special case this to avoid recursion
    shift
    wp-ssh "$@"
    ;;
  # advanced ones
  recv|serve|xrender)
    "$@"
    ;;
  # demo
  sendrecv-demo)
    "$@"
    ;;
  --help|-h)
    help
    ;;
  *)
    # uncomment to run internal functions
    #"$@"
    die "wp: Invalid action '$1'"
    ;;
esac

