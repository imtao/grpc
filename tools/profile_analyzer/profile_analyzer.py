#!/usr/bin/env python
# Copyright 2015, Google Inc.
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are
# met:
#
#     * Redistributions of source code must retain the above copyright
# notice, this list of conditions and the following disclaimer.
#     * Redistributions in binary form must reproduce the above
# copyright notice, this list of conditions and the following disclaimer
# in the documentation and/or other materials provided with the
# distribution.
#     * Neither the name of Google Inc. nor the names of its
# contributors may be used to endorse or promote products derived from
# this software without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
# "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
# LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR
# A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT
# OWNER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL,
# SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT
# LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE,
# DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY
# THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
# (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
# OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

"""
Read GRPC basic profiles, analyze the data.

Usage:
  bins/basicprof/qps_smoke_test > log
  cat log | tools/profile_analyzer/profile_analyzer.py
"""


import collections
import itertools
import math
import re
import sys

# Create a regex to parse output of the C core basic profiler,
# as defined in src/core/profiling/basic_timers.c.
_RE_LINE = re.compile(r'GRPC_LAT_PROF ' +
                      r'([0-9]+\.[0-9]+) 0x([0-9a-f]+) ([{}.!]) ([0-9]+) ' +
                      r'([^ ]+) ([^ ]+) ([0-9]+)')

Entry = collections.namedtuple(
    'Entry',
    ['time', 'thread', 'type', 'tag', 'id', 'file', 'line'])


class ImportantMark(object):
  def __init__(self, entry, stack):
    self._entry = entry
    self._pre_stack = stack
    self._post_stack = list()
    self._n = len(stack)  # we'll also compute times to that many closing }s

  @property
  def entry(self):
    return self._entry

  def append_post_entry(self, post_entry):
    if self._n > 0 and post_entry.thread == self._entry.thread:
      self._post_stack.append(post_entry)
      self._n -= 1

  def get_deltas(self):
    pre_and_post_stacks = itertools.chain(self._pre_stack, self._post_stack)
    return collections.OrderedDict((stack_entry,
                                   abs(self._entry.time - stack_entry.time))
                                   for stack_entry in pre_and_post_stacks)


def print_grouped_imark_statistics(group_key, imarks_group):
  values = collections.OrderedDict()
  for imark in imarks_group:
    deltas = imark.get_deltas()
    for relative_entry, time_delta_us in deltas.iteritems():
      key = '{tag} {type} ({file}:{line})'.format(**relative_entry._asdict())
      l = values.setdefault(key, list())
      l.append(time_delta_us)

  print group_key
  print '{:>40s}: {:>15s} {:>15s} {:>15s} {:>15s}'.format(
        'Relative mark', '50th p.', '90th p.', '95th p.', '99th p.')
  for key, time_values in values.iteritems():
    time_values = sorted(time_values)
    print '{:>40s}: {:>15.3f} {:>15.3f} {:>15.3f} {:>15.3f}'.format(
          key, percentile(time_values, 50), percentile(time_values, 90),
          percentile(time_values, 95), percentile(time_values, 99))
  print


def entries():
  for line in sys.stdin:
    m = _RE_LINE.match(line)
    if not m: continue
    yield Entry(time=float(m.group(1)),
                thread=m.group(2),
                type=m.group(3),
                tag=int(m.group(4)),
                id=m.group(5),
                file=m.group(6),
                line=m.group(7))

threads = collections.defaultdict(lambda: collections.defaultdict(list))
times = collections.defaultdict(list)
important_marks = collections.defaultdict(list)
stack_depth = 0

for entry in entries():
  thread = threads[entry.thread]
  if entry.type == '{':
    thread[entry.tag].append(entry)
    stack_depth += 1
  if entry.type == '!':
    # Save a snapshot of the current stack inside a new ImportantMark instance.
    # Get all entries _for any tag in the thread_.
    stack = [e for entries_for_tag in thread.itervalues()
               for e in entries_for_tag]
    imark_group_key = '{tag}/{thread}@{file}:{line}'.format(**entry._asdict())
    important_marks[imark_group_key].append(ImportantMark(entry, stack))
  elif entry.type == '}':
    last = thread[entry.tag].pop()
    times[entry.tag].append(entry.time - last.time)
    # Update accounting for important marks.
    for imarks_group in important_marks.itervalues():
      # only access the last "stack_depth" imarks.
      for imark in imarks_group[-stack_depth:]:
        imark.append_post_entry(entry)
    stack_depth -= 1

def percentile(vals, percent):
  """ Calculates the interpolated percentile given a sorted sequence and a
  percent (in the usual 0-100 range)."""
  assert vals, "Empty input sequence."
  percent /= 100.0
  k = (len(vals)-1) * percent
  f = math.floor(k)
  c = math.ceil(k)
  if f == c:
      return vals[int(k)]
  # else, interpolate
  d0 = vals[int(f)] * (c-k)
  d1 = vals[int(c)] * (k-f)
  return d0 + d1

print 'tag 50%/90%/95%/99% us'
for tag in sorted(times.keys()):
  vals = sorted(times[tag])
  print '%d %.2f/%.2f/%.2f/%.2f' % (tag,
                                    percentile(vals, 50),
                                    percentile(vals, 90),
                                    percentile(vals, 95),
                                    percentile(vals, 99))

print
print 'Important marks:'
print '================'
for group_key, imarks_group in important_marks.iteritems():
  print_grouped_imark_statistics(group_key, imarks_group)
