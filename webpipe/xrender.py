#!/usr/bin/python
"""
xrender.py

A filter that reads filenames from stdin, and prints HTML directories on
stdout.

File types:

- .png -> inline HTML images (data URIs) 
- .csv -> table

Next:

- .script -- type script for Shell session
  - a configurable prefix, like "hostname; whoami;" etc. would be useful.
- .grep -- grep results
  - But then do we have to copy a ton of files over?
  - xrender needs have access to the original directory.
  - and the command

Ideas:

- .url file -> previews of URLs?
- .foo-url -> previews of a certain page?

Should this call a shell script then?  Or it could be a shell script?  The
function will use the tnet tool?

TODO: Make this usable as a library too?  So in the common case you can have a
single process.

Plugins
-------

See comments below for the interface.

"Style Guide".  Things plugins should do:

- check size of file (whether in bytes, entries, depth, etc.)
  - small: just display it inline (and possibly summary)
  - large: display summary, then click through to full view
  - huge: display summary, and message that says it's too big to preview

- summary:
  - size in bytes
  - entries, etc.

- provide original file for download (in most cases)

- zero copy
  - if you make a symlink, then the plugin can read that stuff, create a summary
  - and then can it output a *capability* for the server to serve files
    anywhere on the file system?
    - or perhaps the symlink is enough?  well it could change.
    - maybe you have to dereference the link.
"""

import cgi
import csv
import errno
import json
import os
import re
import subprocess
import sys

import jsontemplate
import tnet


class Error(Exception):
  pass


# See http://datatables.net/usage/
# CDN: http://www.asp.net/ajaxlibrary/CDNjQueryDataTables194.ashx

# TODO:
# - generate a different table ID for each one, and then style only that?
#   - I think you can generate the ID in the web roll.  That makes a lot more
#   sense, since it's dynamic.
#   - <div class="roll-part" id="part1"> 
# - don't want every plugin to hard code jquery.getScript
#   - would be nicer to present <elem js="http://" css="http://">

TABLE_TEMPLATE = jsontemplate.Template("""\

<link rel="stylesheet" type="text/css"
      href="http://ajax.aspnetcdn.com/ajax/jquery.dataTables/1.9.4/css/jquery.dataTables.css" />

<script type="text/javascript">
var dtjs="http://ajax.aspnetcdn.com/ajax/jquery.dataTables/1.9.4/jquery.dataTables.min.js";

//$.getScript(dtjs, function() {
//  // NOTE: Is this inefficient?  When you have a lot of tables, it's doing
//  // everything.  Another approach is to generate a unique ID.  But user
//  // scripts might not have that benefit?
//
//  $('.data-table').dataTable();
//});

// Using Chrome dev tools, we can that the JS becomes a cache hit, while
// $.getScript() explicitly breaks caches.

$.ajax({
  url: dtjs,
  dataType: "script",
  cache: true,  // avoid loading every time
  success: function() {
    $('.data-table').dataTable();
  }
});

</script>

<table class="data-table" align="center">
  <thead>
    <tr> {.repeated section thead} <th>{@}</th> {.end} </tr>
  </thead>
  <tbody>
    {.repeated section rows}
      <tr> {.repeated section @} <td>{@}</td> {.end} </tr>
    {.end}
  </tbody>
</table>

<p>
  <a href="{orig_url|htmltag}">{orig_anchor}</a>
</p>
""", default_formatter='html')


if sys.stderr.isatty():
  PREFIX = '\033[36m' + 'xrender:' + '\033[0;0m'
else:
  PREFIX = 'xrender:'

def log(msg, *args):
  if args:
    msg = msg % args
  print >>sys.stderr, PREFIX, msg


BAD_RE = re.compile(r'[^a-zA-Z0-9_\-]')

def CleanFilename(filename):
  """Return an escaped filename that's both HTML and shell safe.

  If it weren't shell safe, then
  
    cp $input $output

  Would fail if $input had spaces.

  If it weren't HTML safe, then

    <a href="$output"></a>

  would result in XSS if $output had a double quote.

  We use @hh, where h is a hex digit.  For example, @20 for space.
  """
  assert isinstance(filename, str)  # byte string, not unicode
  return BAD_RE.sub(lambda m: '@%x' % ord(m.group(0)), filename)


def RenderCsv(orig_rel_path, filename, contents):
  """
  Turn CSV into an HTML table.

  TODO: maximum number of rows.
  """
  lines = contents.splitlines()
  c = csv.reader(lines)
  d = {'rows': [], 'orig_url': orig_rel_path, 'orig_anchor': filename}

  for i, row in enumerate(c):
    #print 'R', row
    if i == 0:
      d['thead'] = row
    else:
      d['rows'].append(row)
  #print d
  return TABLE_TEMPLATE.expand(d), None


# TODO: use mime types here?
# The two-level hierarchy of:
# image/png, image/gif, etc. might be useful
#
# Also: aliases like htm, html, etc. are detected

def GuessFileType(filename):
  filename, ext = os.path.splitext(filename)
  if ext == '':
    # The 'script' command defaults to a file called 'typescript'.  We assume
    # the terminal is ansi, so we use the ansi plugin to handle it.
    if filename == 'typescript':
      return 'ansi'
    else:
      return None
  else:
    # .png -> png
    return ext[1:]

  return file_type


BUILTINS = {
    'csv': RenderCsv,
    }


class Resources(object):
  def __init__(self, package_dir=None):
    this_dir = os.path.dirname(sys.argv[0])
    self.package_dir = package_dir or os.path.dirname(this_dir)
    self.user_dir = os.path.expanduser('~/webpipe')

  def GetPluginBin(self, file_type):
    # plugins dir is parallel to webpipe python dir.
    p = os.path.join(self.package_dir, 'plugins', file_type, 'render')
    u = os.path.join(self.user_dir, 'plugins', file_type, 'render')

    # TODO: test if it's executable.  Show clear error if not.
    if os.path.exists(p):
      return p
    if os.path.exists(u):
      return u
    return None


def main(argv):
  """Returns an exit code."""

  # NOTE: This is the input base path.  We just join them with the filenames on
  # stdin.
  in_dir = argv[1]
  out_dir = argv[2]
  # TODO:
  # - input is a single line for now.  Later it could be a message, if you want
  # people to specify an explicit file type.  I guess that can be done with a
  # file extension too, like typescript.ansi.  The problem is that you can't
  # get any other options with it.
  # - output is pointer to files/dirs written.

  res = Resources()

  entries = os.listdir(out_dir)
  nums = []
  for e in entries:
    m = re.match(r'(\d+)\.html', e)
    if m:
      nums.append(int(m.group(1)))

  if nums:
    maximum = max(nums)
  else:
    maximum = 0

  counter = maximum + 1  # application is 1-indexed
  log('counter initialized to %d', counter)

  # e.g. we are about to write "1"
  header = json.dumps({'stream': 'netstring', 'nextPart': counter})

  # Print it on a single line.  Also allow netstring parsing.  Minimal
  # JSON/netstring header is: 2:{}\n.
  sys.stdout.write(tnet.dump_line(header))

  while True:
    line = sys.stdin.readline()
    if not line:
      break

    # TODO: If file contains punctuation, escape it to be BOTH shell and HTML
    # safe, and then MOVE It to ~/webpipe/safe-name
    filename = line.strip()

    # NOTE: Right now, this allows absolute paths too.
    input_path = os.path.join(in_dir, filename)

    # TODO: Plugins should be passed directories directly.
    if os.path.isdir(input_path):
      log('Skipping directory %s (for now)', input_path)
      continue

    # TODO: handle errors
    with open(input_path) as f:
      contents = f.read()

    orig_rel_path = '%d/%s' % (counter, filename)
    orig = None  # original contents

    file_type = GuessFileType(filename)
    log('file type: %s', file_type)

    if file_type is None:
      log("Couldn't determine file type for %r; ignored", filename)
      continue

    out_html_filename = '%d.html' % counter
    out_html_path = os.path.join(out_dir, out_html_filename)

    # Order of resolution:
    #
    # 1. Check user's ~/webpipe dir for plugins
    # 2. Check installation dir for plugins distributed with the webpipe
    #    package
    # 3. Builtins

    plugin_bin = res.GetPluginBin(file_type)
    if plugin_bin:

      # protocol is:
      # render <input> <output>
      #
      # output is just "3".  You are allowed to create the file 3.html, and
      # optionally the *directory* 3.
      #
      # You must print all the files you create to stdout, and output nothing
      # else.
      #
      # Other tools may output stuff on stdout.  You should redirect them to
      # stderr with: 1>&2.  stderr could show up in debug output on the web
      # page (probably only if the exit code is 1?)
      #
      # In the error case, xrender.py should write 3.html, along with a log
      # file?  The html should preview it, but only if it's long.  Use the .log
      # viewer.
      #
      # NOTE: In the future, we could pass $WEBPIPE_ACTION if we want a
      # different type of rendering?

      argv = [plugin_bin, input_path, str(counter)]
      log('argv: %s cwd %s', argv, out_dir)
      exit_code = subprocess.call(argv, cwd=out_dir)
      if exit_code != 0:
        log('ERROR: %s exited with code %d', argv, exit_code)
        with open(out_html_path, 'w') as f:
          # TODO:
          # - make a nicer template.  
          # - show stderr
          f.write('ERROR: %s exited with code %d' % (argv, exit_code))
        print out_html_filename
        counter += 1
        continue

      # Check that the plugin actually create the file.
      if not os.path.exists(out_html_path):
        log('Plugin error: %r not created', out_html_path)
        with open(out_html_path, 'w') as f:
          f.write('Plugin error: %r not created' % out_html_path)
        print out_html_filename

        # TODO: Remove this counter duplication.  Failing here would make it
        # hard to develop plugins.
        counter += 1
        continue

    else:
      # TODO:
      # - use a chaining pattern instead of nested if-else
      # - use a similar: input and output

      # import csv_plugin
      # csv_plugin.main(argv, cwd=cwd)
      # it writes files

      func = BUILTINS.get(file_type)
      if func:
        html, orig = func(orig_rel_path, filename, contents)
        if orig:
          orig_out_path = os.path.join(out_dir, orig_rel_path)

          try:
            os.makedirs(os.path.dirname(orig_out_path))
          except OSError, e:
            if e.errno != errno.EEXIST:
              raise

          with open(orig_out_path, 'w') as f:
            f.write(orig)
          # Print the directory, because we wrote a file there.
          print '%d/' % counter

        with open(out_html_path, 'w') as f:
          f.write(html)
        # This triggers the server
        print out_html_filename

      else:
        log('No builtin renderer for %r; ignored', filename)
        continue

    counter += 1

  return 0


if __name__ == '__main__':
  try:
    sys.exit(main(sys.argv))
  except KeyboardInterrupt:
    print >>sys.stderr, 'xrender: Stopped'
    sys.exit(0)
  except Error, e:
    print >> sys.stderr, e.args[0]
    sys.exit(1)