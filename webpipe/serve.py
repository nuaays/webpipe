#!/usr/bin/python
#
# Copyright 2014 Google Inc. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be found
# in the LICENSE file or at https://developers.google.com/open-source/licenses/bsd

"""
webpipe.py

Server that receives content from a pipe, and serves it "interactively" to the
browser.  It relies on a "hanging GET" -- jQuery on the client and
threading.Event on the server.
"""

import datetime
import errno
import getpass
import json
import optparse
import os
import Queue
import threading
import string  # for lower case letters
import sys

from common import httpd
from common import util
from common import spy

import handlers

# outside
import tnet

log = util.Logger(util.ANSI_BLUE)


class Error(Exception):
  pass


_verbose = False

def debug(msg, *args):
  if _verbose:
    common.log(msg, *args)


class ReadStdin(object):
  """Read filenames from stdin and put them on a queue."""

  def __init__(self, q):
    """
    Args:
      q: Queue, can be None
    """
    self.q = q

  def __call__(self):
    while True:
      # must be unbuffered
      line = sys.stdin.readline()
      if not line:
        break

      line = line.rstrip()
      self.q.put(line)


class Notify(object):
  """Thread to read from queue and notify waiter."""

  def __init__(self, q, waiter):
    """
    Args:
      q: Queue
      waiter: SequenceWaiter object
    """
    self.q = q
    self.waiter = waiter

  def __call__(self):
    # take care of index.html ?  Is this the right way to do it?
    #unused = self.q.get()
    i = 0
    while True:
      name = self.q.get()
      log('notify: %s', name)

      # TODO: do a better check
      if not name.endswith('.html'):
        log('skipped: %s', name)
        continue

      self.waiter.Notify()

      i += 1


def SuffixGen():
  """Generate a readable suffix for a session name

  (when more than one is used in a day)
  """
  i = ord('a')
  for c in string.lowercase:
    yield '-' + c

  # These shouldn't get used because people shouldn't start more than 26
  # sessions in a day.  But we prefix them with -z so they will sort later (at
  # least z0 through z9 will)
  i = 0
  while True:
    yield '-z' + str(i)
    i += 1


def MakeSession(out_dir):
  prefix = datetime.datetime.now().strftime('%Y-%m-%d')
  suffix = ''
  s = SuffixGen()
  while True:
    session = prefix + suffix
    full_path = os.path.join(out_dir, session)
    if not os.path.exists(full_path):
      os.makedirs(full_path)
      log('Created session dir %s', full_path)
      break
    suffix = s.next()
  return session, full_path


def Serve(opts, scroll_path, waiter, package_dir):
  # Pipeline:
  # Read stdin messages -> notify server

  header_line = sys.stdin.readline()
  # skip over length prefix
  i = header_line.find(':')
  if i == -1:
    raise Error('Expected colon in header line: %r' % header_line)

  header = json.loads(header_line[i+1:])
  log('received header %r', header)

  next_part = header.get('nextPart')
  if next_part is not None:
    if isinstance(next_part, int):
      waiter.SetCounter(next_part)
      log('received counter state in header: %d', next_part)
    else:
      log('Ignored invalid nextPart %r', next_part)

  q = Queue.Queue()

  r = ReadStdin(q)
  t1 = threading.Thread(target=r)
  t1.setDaemon(True)  # So Ctrl-C works
  t1.start()

  n = Notify(q, waiter)
  t2 = threading.Thread(target=n)
  t2.setDaemon(True)  # So Ctrl-C works
  t2.start()

  scroll_name = os.path.basename(scroll_path)

  handler_class = handlers.WaitingRequestHandler
  handler_class.user_dir = opts.user_dir
  handler_class.package_dir = package_dir
  handler_class.waiters = {scroll_name: waiter}
  handler_class.active_scroll = scroll_name

  s = httpd.ThreadedHTTPServer(('', opts.port), handler_class)

  # TODO: add opts.hostname?
  log('Serving at http://localhost:%d/s/%s  (Ctrl-C to quit)', opts.port,
      scroll_name)
  s.serve_forever()

  # NOTE: Could do webbrowser.open() after we serve.  But people can also just
  # click the link we printed above, since most terminals will make them URLs.


def CreateOptionsParser():
  parser = optparse.OptionParser('webpipe_main <action> [options]')

  parser.add_option(
      '-v', '--verbose', dest='verbose', default=False, action='store_true',
      help='Write more log messages')
  parser.add_option(
      '-s', '--session', dest='session', type='str', default='',
      help="Name of the session (by default it is based on today's date)")
  parser.add_option(
      '--port', dest='port', type='int', default=8989,
      help='Port to serve on')
  parser.add_option(
      '--length', dest='length', type='int', default=1000,
      help='Length of the scroll, i.e. amount of history to keep.')
  parser.add_option(
      '--num-threads', dest='num_threads', type='int', default=5,
      help='Number of server threads, i.e. simultaneous connections.')

  # scrolls go in the 's' dir, plugins in the 'plugins' dir
  parser.add_option(
      '--user-dir', dest='user_dir', type='str',
      default=util.GetUserDir(),
      help='Per-user directory for webpipe')

  return parser


def AppMain(argv):
  """Returns the length of the scroll created."""

  try:
    action = argv[1]
  except IndexError:
    raise Error('Action required')

  global _verbose
  (opts, _) = CreateOptionsParser().parse_args(argv[2:])
  if opts.verbose:
    _verbose = True

  # Other actions:
  # serve-rendered (or servehtml)
  # refresh

  if action == 'serve':  # TODO: rename to 'serve'
    scroll_path = argv[2]

    # Write index.html in the session dir.
    package_dir = util.GetPackageDir()
    path = os.path.join(package_dir, 'webpipe/index.html')
    with open(path) as f:
      index_html = f.read()

    out_path = os.path.join(scroll_path, 'index.html')
    with open(out_path, 'w') as f:
      f.write(index_html)

    waiter = handlers.SequenceWaiter()
    try:
      Serve(opts, scroll_path, waiter, package_dir)
    except KeyboardInterrupt:
      log('Stopped')
      return waiter.Length()

  elif action == 'noop':
    # For testing latency
    log('noop')

  else:
    raise Error('Invalid action %r' % action)


def main(argv):
  """Returns an exit code."""

  # In this process we send start and end records.  In the xrender process, we
  # send latency for each rendering.
  spy_client = spy.GetClientFromConfig()

  d = {'argv': sys.argv, 'user': getpass.getuser()}
  spy_client.SendRecord('start', d)

  # TODO: also report unhandled exceptions.  The ones in the serving thread are
  # caught by a library though -- we should get at them.
  try:
    length = AppMain(sys.argv)
    d = {'scroll-length': length}
    spy_client.SendRecord('end', d)
  except Error, e:
    log('%s', e.args[0])
    return 1

  return 0


if __name__ == '__main__':
  sys.exit(main(sys.argv))
