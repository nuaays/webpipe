#!/usr/bin/python
#
# Copyright 2014 Google Inc. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be found
# in the LICENSE file or at https://developers.google.com/open-source/licenses/bsd

"""
httpd.py
"""

import BaseHTTPServer
import os
import posixpath
import SimpleHTTPServer
import SocketServer
import urllib


class ThreadedHTTPServer(SocketServer.ThreadingMixIn,
                         BaseHTTPServer.HTTPServer):
  """
  The main reason we inherit from HTTPServer instead of SocketServer is that it
  sets allow_reuse_address, which prevents the issue where we can't bind the
  same port for a period of time after restarting.

  NOTE: This doesn't use a thread pool or anything.  It will just start a new
  thread for each request.
  
  For webpipe, since every thread will block waiting for the next part of the
  scroll, you can create a huge number of threads just by having a huge number
  of clients.  But since this is mainly a single-user server, it doesn't
  matter.

  TODO: There's a still a Ctrl-C bug here, because I think the request threads
  get blocked on the threading.Event().  Need to setDaemon() all threads,
  including the ones that the web server makes.
  """
  # override class variable in ThreadingMixIn.  This makes it so that Ctrl-C works.
  daemon_threads = True


class BaseRequestHandler(SimpleHTTPServer.SimpleHTTPRequestHandler):
  """
  NOTE: The structure of Python's SimpleHTTPServer / BaseHTTPServer is quite
  bad.  But we are reusing it for now, since it is built in to the standard
  library, and it gives "Apache-like" static serving semantics.

  If we end up having to hack this up too much, it might be worth it to write
  our own (or at least copy and modify that code, rather than this fragile
  inheritance.
  """
  server_version = None
  root_dir = None

  def url_to_fs_path(self, url):
    """Translate a URL to a local file system path.

    By default, we just treat URLs as paths relative to self.root_dir.

    If it returns None, then a 404 is generated, without looking at disk.

    Called from send_head() (see SimpleHTTPServer).

    NOTE: This is adapted from Python stdlib SimpleHTTPServer.py.  I just
    changed os.getcwd() to self.root_dir.
    """
    words = [p for p in url.split('/') if p]

    path = self.root_dir  # note: class variable

    # TODO: This can be cleaned up.  Should just be os.path.join.
    for word in words:
      drive, word = os.path.splitdrive(word)
      head, word = os.path.split(word)
      if word in (os.curdir, os.pardir):  # . ..
        continue
      path = os.path.join(path, word)
    return path

  # Copied from stdlib SimpleHTTPServer.py.  The code isn't really extensible
  # so we have to copy it.
  def send_head(self):
    """Common code for GET and HEAD commands.

    This sends the response code and MIME headers.

    Return value is either a file object (which has to be copied
    to the outputfile by the caller unless the command was HEAD,
    and must be closed by the caller under all circumstances), or
    None, in which case the caller has nothing further to do.

    """
    path = self.path
    # Query params aren't relevant to looking up a path.
    #
    # NOTE: Fragment should never be sent by the browser.  Python stdlib
    # originally had this.
    path = path.split('?',1)[0]
    path = path.split('#',1)[0]
    # eliminates double slashes, etc.
    path = posixpath.normpath(urllib.unquote(path))

    path = self.url_to_fs_path(path)
    if path is None:
        self.send_error(404, "File not found")
        return None

    f = None
    if os.path.isdir(path):
        if not self.path.endswith('/'):
            # redirect browser - doing basically what apache does
            self.send_response(301)
            self.send_header("Location", self.path + "/")
            self.end_headers()
            return None
        for index in "index.html", "index.htm":
            index = os.path.join(path, index)
            if os.path.exists(index):
                path = index
                break
        else:
            return self.list_directory(path)
    ctype = self.guess_type(path)
    try:
        # Always read in binary mode. Opening files in text mode may cause
        # newline translations, making the actual size of the content
        # transmitted *less* than the content-length!
        f = open(path, 'rb')
    except IOError:
        self.send_error(404, "File not found")
        return None
    self.send_response(200)
    self.send_header("Content-type", ctype)
    fs = os.fstat(f.fileno())
    self.send_header("Content-Length", str(fs[6]))
    self.send_header("Last-Modified", self.date_time_string(fs.st_mtime))
    self.end_headers()
    return f

