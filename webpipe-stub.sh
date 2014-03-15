#!/bin/sh
#
# This is a self-contained shell script that can be copied to a remote machine.
# It has the minimum necessary stuff to interface with a local webpipe server.
# This way you can view remote files without installing stuff.
#
# NOTE: Uses /bin/sh, not /bin/bash.  In theory this could run on BSD, although
# it hasn't been tested at all.
#
# TODO:
#   init: create ~/webpipe/input and fifo?
#   send: send a specific file
#   sendloop: send files from ~/webpipe fifo
#   print-events?  Move it here they have inotify-tools installed
#
# How to get it to the remote machine:
#
#   $ scp $(webpipe stub-path) user@example.org:bin
#
# Usage:
#   ./webpipe-stub.sh <function name>

set -o nounset

log() {
  echo 1>&2 "$@"
}

# minimal custom TNET encoder.  Avoid dependencies on Python, C, etc.
tnetEncodeFile() {
  local filename=$1
  local remote_name=$2
  local size=$3

  # { file: example.txt, body: foobar }

  local k1='8:filename,'
  # $#s gets the length of s; should be portable
  local v1="${#remote_name}:${remote_name},"
  local k2='4:body,'
  local s2="$size:"

  # 1 for trailing comma
  local v2_size=$(expr ${#s2} + $size + 1)
  local msg_size=$(expr ${#k1} + ${#v1} + ${#k2} + $v2_size)

  echo -n "$msg_size:$k1$v1$k2"

  echo -n $s2
  cat $filename
  echo -n ,
  echo -n }
}

sendHeader() {
  # Send empty dictionary for now.  Send other stream options here in the
  # future.
  echo -n '0:}'
}

# TODO:
# - send filename, optional hostname, optional file type as fields
# - send body as separate message

send() {
  # Add hostname prefix to every filename.
  # TODO: make this a tag instead in the metadata message?
  local hostname
  local prefix
  hostname=$(hostname)
  if test $? -eq 0; then
    prefix="${hostname}_"
  else
    prefix=""
  fi

  sendHeader

  while read filename; do
    local size
    size=$(stat --printf '%s' $filename)  # stat should be portable?
    local exit_code=$?
    if test $exit_code -eq 0; then
      tnetEncodeFile $filename "$prefix$filename" $size
    else
      log "stat error, ignoring $filename"
    fi
  done
}

"$@"
