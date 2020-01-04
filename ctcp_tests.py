#!/usr/bin/env python

import argparse
import os
import signal
import subprocess
import sys
import time
import traceback

from ctypes import *
from random import choice, randint
from subprocess import Popen, PIPE

CTCP_BINARY = "./ctcp"
REFERENCE_BINARY = "./reference"

CLIENT_PORT = str(32843)
SERVER_PORT = str(52365)
REF_PORT = str(39184)

# Number of seconds to wait before timing out a read from STDERR or STDOUT.
TEST_TIMEOUT = 3

CTCP_HEADER_LEN = 20
MAX_SEG_DATA_SIZE = 1440

# Reference program has the following special codes that will help with the
# tester.
#
# ###teardown### = Connection has been torn down.
# ###truncate### - Truncates the segment this data is put into.
# ###badseqno### - Makes this a segment out of the window by changing the
#                  segment's sequence number and computing the correct checksum.
# ###ignore### - Do not send an ACK to this segment.
# ###stop### - Stop processing segments after this one, but continue sending
#              ACKs to received segments.
# ###reorder### - Creates two segments out of this one, and reorders them.
DEBUG_TEARDOWN = "###teardown###"
DEBUG_TRUNCATE = "###truncate###"
DEBUG_BAD_SEQNO = "###badseqno###"
DEBUG_IGNORE = "###ignore###"
DEBUG_STOP = "###stop###"
DEBUG_REORDER = "###reorder###"

run_part1b = False
# Whether or not the sliding window test passed.
sliding_window_passed = False

############################ HELPERS FOR SUBPROCESS  ###########################

class CTCP_SEGMENT(Structure):
  _fields_ = [
    ("seqno", c_uint),
    ("ackno", c_uint),
    ("len", c_ushort),
    ("flags", c_uint),
    ("window", c_ushort),
    ("cksum", c_ushort),
    ("data", c_char_p)
  ]

class Segment:
  """
  Class: Segment
  --------------
  Represents a TCP segment.
  """
  time = 0
  source = ""
  source_port = -1
  dest = ""
  dest_port = -1
  seqno = -1
  ackno = -1
  length = -1
  flags = []
  window = -1
  checksum = ""
  string = ""
  c_repr = None

  def __repr__(self):
    return "Segment: [" + self.string + "]"

  def has_same_flags(self, other):
    if len(self.flags) != len(other.flags):
      return False

    for flag in self.flags:
      if flag not in other.flags:
        return False
    return True

  def convert_flags(self, flags):
    c_flags = 0
    for flag in flags:
      if "ACK" in flag:
        c_flags |= 0x10
      elif "SYN" in flag:
        c_flags |= 0x02
      elif "FIN" in flag:
        c_flags |= 0x01
    return c_flags

  def ctcp_checksum(self):
    saved_checksum = c_repr.cksum
    self.c_repr.cksum = 0
    new_checksum = sum([ord(c) for c in buffer(self.c_repr)])
    self.c_repr.cksum = saved_checksum
    return new_checksum


class TimeoutError(Exception):
  """
  Class: TimeoutError
  -------------------
  Occurs if a read times out.
  """
  def __init__(self, msg):
    self.error_message = msg


class timeout:
  """
  Class: timeout
  --------------
  Timeout decorator. Used for timing out a block of code.
  """
  def __init__(self, seconds=1, error_message='Timeout'):
    self.seconds = seconds
    self.error_message = error_message

  def handle_timeout(self, signum, frame):
    raise TimeoutError(self.error_message)

  def __enter__(self):
    signal.signal(signal.SIGALRM, self.handle_timeout)
    signal.alarm(self.seconds)

  def __exit__(self, type, value, traceback):
    signal.alarm(0)


def start_server(port=SERVER_PORT, flags=[], reference=False):
  """
  Function: start_server
  ----------------------
  Starts a cTCP server.

  reference: Whether or not to use the reference binary.
  """
  binary = REFERENCE_BINARY if reference else CTCP_BINARY
  server = Popen([binary, "-s", "-p", port, "-z"] + flags, stdin=PIPE,
                 stdout=PIPE, stderr=PIPE)
  return server


def start_client(server="localhost", server_port=SERVER_PORT, port=CLIENT_PORT,
                 flags=[], reference=False):
  """
  Function: start_client
  ----------------------
  Starts a cTCP client.

  server: Location of server.
  port: Port to start client at.
  reference: Whether or not to use the reference binary.
  """
  binary = REFERENCE_BINARY if reference else CTCP_BINARY
  client = Popen([binary, "-c", server + ":" + server_port, "-p", port, "-z"] +
                 flags, stdin=PIPE, stdout=PIPE, stderr=PIPE)
  return client


def make_random(length, is_binary=False):
  """
  Makes random data of the specified length.

  length: Length of random data to make.
  is_binary: Whether or not to make it binary data (non-ASCII).
  """
  limit = 255 if is_binary else 126
  return "".join([chr(choice(range(32, limit))) for _ in range(length)]) + "\n"


def read_from(host, num_lines=-1, stderr=False):
  """
  Function: read_from
  -------------------
  Reads from a host's STDOUT or STDERR. Times out after a few seconds if
  nothing is read.

  host: Host to read from.
  num_lines: Number of lines to read. If -1, reads forever until a timeout.
  returns: The message received.
  """
  prev_msg = None
  msg = ""
  try:
    with timeout(seconds=TEST_TIMEOUT):
      while num_lines <= -1 or num_lines > 0:
        prev_msg = msg
        host.stdout.flush()
        msg += host.stderr.readline() if stderr else host.stdout.readline()
        num_lines -= 1
  except TimeoutError:
    return msg

  return msg


def read_segments_from(host):
  """
  Function: read_segments_from
  ----------------------------
  Reads segments sent and received from a host.

  host: Host to read from.
  returns: The segments sent and received.
  """
  log = read_from(host, stderr=True).split("\n")
  segment_logs = [l for l in log if l.startswith("!!!") and l.endswith("!!!")]
  segments = []

  # Convert the segment log into Segment objects for easier handling.
  try:
    for segment_log in segment_logs:
      details = segment_log[3:-3].split("\t")
      segment = Segment()
      segment.time = int(details[0])
      segment.source = details[1]
      segment.source_port = int(details[2])
      segment.dest = details[3]
      segment.dest_port = int(details[4])
      segment.seqno = int(details[5])
      segment.ackno = int(details[6])
      segment.length = int(details[7])
      segment.flags = details[8].split(" ")
      segment.window = int(details[9])
      segment.checksum = details[10]
      segment.string = ",".join(segment_log[3:-3].split("\t"))
      segment.c_repr = CTCP_SEGMENT(
        segment.seqno,
        segment.ackno,
        segment.length,
        segment.convert_flags(segment.flags),
        segment.window,
        int(segment.checksum, 16)
      )
      segments.append(segment)
  except IndexError:
    pass

  return segments


def read_debug_messages_from(host):
  """
  Function: read_ctrl_messages_from
  ---------------------------------
  Reads debug messages used for testing purposes. They include:
    ###teardown### - When connection teardown occurs.

  host: Host to read from.
  returns: A list of debug messages read.
  """
  log = read_from(host, stderr=True).split("\n")
  messages = [l for l in log if l.startswith("###") and l.endswith("###")]
  return messages


def write_to(host, msg):
  """
  Function: write_to
  ------------------
  Writes a message to the specified host's STDIN. This should be read in and
  a segment should be created and sent.

  host: Host to write to.
  msg: Message to write.
  """
  try:
    with timeout(seconds=TEST_TIMEOUT):
      host.stdin.write(msg)
      host.stdin.flush
  except (IOError, TimeoutError):
    pass


#################################### TESTS  ####################################

def client_sends():
  """
  Writes to the student/client's STDIN. Client should create a segment and send
  it to the student/server. Only checks that a segment is sent and contains the
  data (by checking segment length).
  """
  test_str = "t35t1nG cl13nT 53nd1nG\n"
  server = start_server()
  client = start_client()

  write_to(client, test_str)
  segments = read_segments_from(client)
  if not segments:
    return False

  # The first segment should be one sent from the client, and should have the
  # correct length.
  segment = segments[0]
  return (
    str(segment.source_port) == CLIENT_PORT and
    segment.length == CTCP_HEADER_LEN + len(test_str)
  )


def client_receives():
  """
  Writes to the student/client's STDIN. Client should create and send a segment
  to the student/server. Only checks that the segment is received by the server
  and contains data (by checking segment length).
  """
  test_str = "t35t1nG cl13nT r3c31\/1NG\n"
  server = start_server()
  client = start_client()

  write_to(client, test_str)
  segments = read_segments_from(server)
  if not segments:
    return False

  # The first segment should be one received from the client, and should have
  # the correct length.
  segment = segments[0]
  return (
    str(segment.dest_port) == SERVER_PORT and
    segment.length == CTCP_HEADER_LEN + len(test_str)
  )


def correct_checksum():
  """
  Sends two segments. Makes sure they have the correct checksum by comparing
  it to the checksum from the reference solution.
  """
  test_strs = ["ch3ck1nG c0rr3ct ch3cksu|\/|\n", "y3T an0th3r str1ng0_x\/.!&\n"]

  def test_checksum(test_str):
    server = start_server()
    client = start_client()

    write_to(client, test_str)
    segments = read_segments_from(client)
    if not segments:
      return False
    teardown()

    # Start reference solution to get answers.
    ref_server = start_server(port=REF_PORT, reference=True)
    ref_client = start_client(server_port=REF_PORT, reference=True)

    # Get reference checksum.
    write_to(ref_client, test_str)
    ref_segment = read_segments_from(ref_client)[0]
    ref_checksum = ref_segment.checksum

    # Check the first sent segment.
    segment = segments[0]

    # Checksum equal to the reference checksum.
    if segment.checksum == ref_checksum:
      return True

    # Maybe they also set an ACK for this segment. Compare with the computed
    # checksum.
    return int(segment.checksum, 16) == segment.c_repr.cksum;

  return reduce(lambda a, b: a and b, [test_checksum(t) for t in test_strs])


def correct_header_fields():
  """
  Client sends a segment to the server. Makes sure the header fields are all
  set correctly.
  """
  test_str = "c0rrect_!!heAd3R fi3ld5__%%!!     @\n"
  server = start_server()
  client = start_client()

  write_to(client, test_str)
  segments = read_segments_from(client)
  if not segments:
    return False
  teardown()

  # Start reference solution to get answers.
  ref_server = start_server(port=REF_PORT, reference=True)
  ref_client = start_client(server_port=REF_PORT, reference=True)

  # Get reference checksum.
  write_to(ref_client, test_str)
  ref_segment = read_segments_from(ref_client)[0]

  # Check the first sent segment. Should have all the same header fields as
  # the reference.
  segment = segments[0]

  # Check the flags first. Maybe decided to ACK all segments.
  if not segment.has_same_flags(ref_segment):
    if "ACK" in segment.flags:
      segment.flags.remove("ACK")

  return (
    segment.seqno == ref_segment.seqno and
    (segment.ackno == 0 or segment.ackno == ref_segment.ackno) and
    segment.length == ref_segment.length and
    segment.has_same_flags(ref_segment) and
    segment.window == ref_segment.window and
    (segment.checksum == ref_segment.checksum or
     int(segment.checksum, 16) == segment.c_repr.cksum)
  )


def bidirectional():
  """
  Client can both send and receive messages.
  """
  test_str_send = "5tr1NG 53nT 295 !_ __ %#^^^ .\n"
  test_str_recv = "5tr1NG r3c31v3D 224@ &&&~~~~`\n"
  server = start_server()
  client = start_client()

  write_to(client, test_str_send)
  write_to(server, test_str_recv)
  time.sleep(TEST_TIMEOUT)
  sent_str = read_from(server, num_lines=1)
  recv_str = read_from(client, num_lines=1)

  # Make sure the server received the sent string and the client received the
  # string from the server.
  if sent_str != test_str_send or recv_str != test_str_recv:
    return False

  # Now do it in the reverse direction.
  write_to(server, test_str_send)
  write_to(client, test_str_recv)
  time.sleep(TEST_TIMEOUT)
  sent_str = read_from(client, num_lines=1)
  recv_str = read_from(server, num_lines=1)

  return sent_str == test_str_send and recv_str == test_str_recv


def large_data():
  """
  Sends data twice + 1 the window size. It should all be received properly.
  """
  test_str = make_random(MAX_SEG_DATA_SIZE * 2 + 1)
  server = start_server()
  client = start_client()

  write_to(client, test_str)
  time.sleep(TEST_TIMEOUT)
  result = read_from(server)
  return result == test_str


def unreliability(flag):
  """
  Sends segments unreliably from the client to the server.
  """
  test_str = "unr3l14b13 p4ck3t!!!!!      !!!~\n"
  server = start_server()
  client = start_client(flags=[flag, "100"])

  write_to(client, test_str)
  time.sleep(TEST_TIMEOUT)
  return read_from(server) == test_str

def segment_corruption():
  return unreliability("-t")

def segment_drops():
  return unreliability("-r")

def segment_delays():
  return unreliability("-y")

def segment_duplicates():
  return unreliability("-q")


def segment_truncated():
  """
  Sends a complete segment from reference/client to student/server, which
  should be processed correctly. Then sends a truncated segment, which should
  be ignored.
  """
  test_str = "n0t trunc4t3d 139482793 912847 192874 1928\n"
  truncated_str = DEBUG_TRUNCATE + "trunc4t3d 139482793 912847 192874 1928\n"
  server = start_server()
  client = start_client(reference=True)

  # Send full segment.
  write_to(client, test_str)
  time.sleep(TEST_TIMEOUT)
  if read_from(server, num_lines=1) != test_str:
    return False

  # Write the truncated segment. Nothing should be read from the server.
  write_to(client, truncated_str)
  time.sleep(TEST_TIMEOUT)
  if read_from(server, num_lines=1) == truncated_str:
    return False

  return True


def fin_sent():
  """
  Checks to see that a FIN segment is sent when an EOF is read from STDIN.
  """
  test_str = "f1N s3nt\n"
  server = start_server()
  client = start_client()

  # First write some data.
  write_to(client, test_str)
  if not read_segments_from(client):
    return False
  time.sleep(1)

  # Write an EOF character.
  write_to(client, '\x1a')
  client.stdin.close()

  # Check to see that segment sent from client is a FIN.
  segments = read_segments_from(client)
  if not segments:
    return False
  return "FIN" in segments[0].flags


def connection_teardown():
  """
  Makes sure connection teardown occurs when both sides send a FIN.
  """
  test_str = make_random(100)
  server = start_server()
  client = start_client()

  # First write some data at both ends.
  write_to(client, test_str)
  write_to(server, test_str)
  time.sleep(TEST_TIMEOUT)

  # Write EOFs on both sides.
  write_to(client, '\x1a')
  write_to(server, '\x1a')
  client.stdin.close()
  server.stdin.close()
  time.sleep(TEST_TIMEOUT)

  return (
    DEBUG_TEARDOWN in read_debug_messages_from(client) and
    DEBUG_TEARDOWN in read_debug_messages_from(server)
  )


def larger_windows():
  """
  Sets a larger window size for student/client and reference/server.
  Reference/server immediately stops processing data and only sends repeated
  ACKs. Student/client should send up to the large window size (4 *
  MAX_SEG_DATA_SIZE), but not less than (3 * MAX_SEG_DATA_SIZE), otherwise, they
  aren't even using the larger window size.
  """
  global sliding_window_passed

  stop_str = DEBUG_STOP + "1t'5 h4mm3r t1m3!!!!!!!!\n"
  large_strs = [make_random(596) for _ in range(20)]
  server = start_server(reference=True, flags=["-w", str(4)])
  client = start_client(flags=["-w", str(4)])

  # Stop the server from processing anything.
  write_to(client, large_strs[0])
  read_segments_from(client)
  write_to(client, stop_str)
  server_segments = read_segments_from(server)
  if not server_segments:
    return False

  # Get the last ackno from server.
  last_ackno = server_segments[-1].ackno

  # Have the client send a lot of data. See if it sends up to the window size.
  for large_str in large_strs:
    write_to(client, large_str)
  segments = read_segments_from(server)
  if not segments:
    return False

  # Look only at segments sent by client.
  segments = [s for s in segments if s.source_port == int(CLIENT_PORT)]
  if len(segments) == 0:
    return False

  # Get the largest segment sent.
  largest_seg = max(segments, key=lambda s: s.seqno)
  passed = largest_seg.seqno <= last_ackno + 4 * MAX_SEG_DATA_SIZE and \
           largest_seg.seqno >= last_ackno + 3 * MAX_SEG_DATA_SIZE
  sliding_window_passed = passed
  return passed


def different_windows():
  """
  Sets a larger window size for student/client and reference/server. The
  send and receive window for the student/client is different. Reference/server
  immediately stops processing data and only sends repeated ACKs. Student/client
  should send up to the large window size (8 * MAX_SEG_DATA_SIZE), but not less
  than (6 * MAX_SEG_DATA_SIZE). They should not be using the receive window
  size.
  """
  stop_str = DEBUG_STOP + "1t'5 h4mm3r t1m322222222!!!!!!!!\n"
  large_strs = [make_random(596) for _ in range(20)]
  server = start_server(reference=True, flags=["-w", str(8)])
  client = start_client(flags=["-w", str(2)])

  # Stop the server from processing anything.
  write_to(client, large_strs[0])
  read_segments_from(client)
  write_to(client, stop_str)
  server_segments = read_segments_from(server)
  if not server_segments:
    return False

  # Get the last ackno from server.
  last_ackno = server_segments[-1].ackno

  # Have the client send a lot of data. See if it sends up to the window size.
  for large_str in large_strs:
    write_to(client, large_str)
  segments = read_segments_from(server)
  if not segments:
    return False

  # Look only at segments sent by client.
  segments = [s for s in segments if s.source_port == int(CLIENT_PORT)]
  if len(segments) == 0:
    return False

  # Get the largest segment sent.
  largest_seg = max(segments, key=lambda s: s.seqno)
  return largest_seg.seqno <= last_ackno + 8 * MAX_SEG_DATA_SIZE and \
         largest_seg.seqno >= last_ackno + 6 * MAX_SEG_DATA_SIZE


def sets_window_size():
  """
  Makes sure the window size field is set.
  """
  test_str = make_random(596)
  server = start_server(reference=True)
  client = start_client(flags=["-w", str(8)])

  write_to(client, test_str)
  segments = read_segments_from(client)
  if not segments:
    return False

  return segments[0].window == 8 * MAX_SEG_DATA_SIZE


def multiple_clients():
  """
  Connect 4 clients to the server. There should be no problems.
  """
  server = start_server()
  clients = [start_client(port=str(p)) for p in range(11110, 11114)]
  test_strs = [make_random(100) for _ in range(4)]

  # Write to all clients. Expect the server to receive all their output.
  for i, client in enumerate(clients):
    write_to(client, test_strs[i])
    time.sleep(1)
    if not read_from(server) == test_strs[i]:
      return False
  return True


def ping_pong():
  """
  Ping-pongs short messages back and forth between the client and server.
  """
  test_strs = [make_random(100) for i in range(10)]
  server = start_server()
  client = start_client()

  # Send messages back and forth between client and server.
  for i in range(len(test_strs) / 2):
    write_to(client, test_strs[2 * i])
    write_to(server, test_strs[2 * i + 1])
    time.sleep(TEST_TIMEOUT)

    # If messages are not received properly, error!
    if read_from(client, num_lines=1) != test_strs[2 * i + 1] or \
       read_from(server, num_lines=1) != test_strs[2 * i]:
      return False

  return True


def flow_control():
  """
  Student/client sends a lot of data to reference/server. Reference/server stops
  responding after a while. Student/client should send up to the server's
  window and then stop sending (practicing flow control).
  """
  test_strs = [make_random(288) for _ in range(10)]
  stop_str = DEBUG_STOP + "1t'5 h4mm3r t1m3!!!!!!!!\n"
  server = start_server(reference=True)
  client = start_client()

  # First write some segments to the server, then tell it to stop processing
  # segments. Get the last ackno from the server.
  write_to(client, test_strs[0])
  write_to(client, test_strs[1])
  time.sleep(TEST_TIMEOUT)
  read_segments_from(client)
  write_to(client, stop_str)
  server_segments = read_segments_from(server)
  if not server_segments:
    return False
  last_ackno = server_segments[-1].ackno

  # Send more segments.
  for i in range(2, len(test_strs)):
    write_to(client, test_strs[i])

  # Look at the last segment sent by the client.
  segments = read_segments_from(server)
  if not segments:
    return False
  segment = [s for s in segments if s.source_port == int(CLIENT_PORT)][-1]

  # If this sequence number is greater than the window size, then no flow
  # control was done.
  return segment.seqno <= last_ackno + MAX_SEG_DATA_SIZE


def reorders():
  """
  Reference/client sends out-of-order segments to client/server. Expects them
  to be reordered properly and outputted.
  """
  test_str = DEBUG_REORDER + "r30rd3r1ng FuN\n"
  server = start_server()
  client = start_client(reference=True)

  # Send control string to client. Client will break this apart into two
  # segments, the first one with the string test_str, and the second one with
  # just DEBUG_REORDER. It will then send these in the wrong order to the
  # server.
  write_to(client, test_str)
  time.sleep(TEST_TIMEOUT)
  return read_from(server) == test_str + DEBUG_REORDER + "\n"


def interoperation():
  """
  Student/client and reference/server, and vice-versa.
  """
  # Start client and reference server.
  test_str = make_random(100)
  ref_server = start_server(reference=True)
  client = start_client()

  # Write from client to reference server.
  write_to(client, test_str)
  if not read_from(ref_server) == test_str:
    return False

  # Write from reference server to client.
  test_str = make_random(100)
  write_to(ref_server, test_str)
  if not read_from(client) == test_str:
    return False

  return True


def no_excessive_retrans():
  """
  Makes sure there are only 5 retransmissions of a segment (6 total
  transmissions. Sends a segment from student/client to reference/server.
  Reference/server will ignore the segment.
  """
  test_str = DEBUG_IGNORE + "r3tr4n5m15510ns~~~~~~~\n"
  server = start_server(reference=True)
  client = start_client()

  # Send a segment to reference server, which should ignore it. See how many
  # times it was sent.
  write_to(client, test_str)
  segments = read_segments_from(server)
  if not segments or len(segments) != 6:
    return False

  # All segments should have the same content.
  orig_segment = segments[0]
  for segment in segments:
    if (
      segment.source != orig_segment.source or
      segment.source_port != orig_segment.source_port or
      segment.dest != orig_segment.dest or
      segment.dest_port != orig_segment.dest_port or
      segment.seqno != orig_segment.seqno or
      segment.ackno != orig_segment.ackno or
      segment.length != orig_segment.length or
      not segment.has_same_flags(orig_segment) or
      segment.window != orig_segment.window or
      segment.checksum != orig_segment.checksum
    ):
      return False

  return True


def ignores_bad_seqno():
  """
  Sends a complete segment from reference/client to student/server, which
  should be processed correctly. Then sends a segment with a sequence number
  completely out of scope, which should be ignored.
  """
  test_str = "cs144--cs144--cs144--cs144--cs144--cs144--cs144--cs144\n"
  bad_seqno_str = DEBUG_BAD_SEQNO + "cs144cs144cs144cs144cs144cs144cs144cs144\n"
  server = start_server()
  client = start_client(reference=True)

  # Send full segment.
  write_to(client, test_str)
  time.sleep(TEST_TIMEOUT)
  if read_from(server, num_lines=1) != test_str:
    return False
  segments = read_segments_from(server)
  first_segment = segments[0] if len(segments) > 0 else None

  # Write the bad segment. Nothing should be read from the server and no
  # ACKs should be sent.
  write_to(client, bad_seqno_str)
  time.sleep(TEST_TIMEOUT)
  if read_from(server, num_lines=1) == bad_seqno_str:
    return False

  # Make sure no ACKs are sent to the bad segment, or if an ACK is sent,
  # it is a duplicate ACK to a previous segment.
  segments = read_segments_from(server)
  if not segments:
    return False
  for segment in segments:
    if "ACK" in segment.flags and segment.source_port == CLIENT_PORT and \
      (first_segment is None or segment.ackno != first_segment.ackno):
      return False

  return True

def send_after_fin():
  """
  Student/server receives FIN. It should still send data to the client. Checks
  that a FIN was received first.
  """
  test_str = make_random(100)
  test_str_fin = "s3nd 4ft3r f1N\n"
  server = start_server()
  client = start_client()

  # Write an EOF character to client so it sends a FIN.
  write_to(server, test_str)
  write_to(client, '\x1a')
  client.stdin.close()

  # Check that a FIN was received.
  time.sleep(1)
  segments = read_segments_from(server)
  if not segments:
    return False
  if not "FIN" in [flag for segment in segments for flag in segment.flags]:
    return False

  # Write to server STDIN. It should continue sending data to the client.
  write_to(server, test_str_fin)
  return len(read_segments_from(server)) > 0


def recv_after_eof():
  """
  Client reads an EOF and should send a FIN. It should still be able to receive
  data from the server.
  """
  test_str = make_random(100)
  test_str_fin = "r3c31v3 4ft3r f1N\n"
  server = start_server()
  client = start_client()

  # Write an EOF character to client so it sends a FIN.
  write_to(server, test_str)
  write_to(client, '\x1a')
  client.stdin.close()

  # Check that a FIN was sent.
  time.sleep(1)
  segments = read_segments_from(client)
  if not segments:
    return False
  if not "FIN" in [flag for segment in segments for flag in segment.flags]:
    return False

  # Write to server STDIN. The client should receive and output the data.
  write_to(server, test_str_fin)
  return test_str_fin in read_from(client)

def ask_google():
    """
    Send a request for www.google.com home page. Get back response. As long
    as 1 or more segments are received, declare success.
    """

    request = "GET / HTTP/1.1\nHost: www.google.com\n\n"
    client_port = randint(10000, 65535)
    client = start_client(server="www.google.com", server_port=str(80), port = str(client_port))
    write_to(client,request)
    segments = read_segments_from(client)

    if not segments or len(segments) == 0:
        return False

    if len(segments) > 50:
        return True
    else:
        return False

def ask_bing():
    """
    Send a request for www.google.com home page. Get back response. As long
    as 1 or more segments are received, declare success.
    """

    request = "GET / HTTP/1.1\nHost: www.bing.com\n\n"
    client_port = randint(10000, 65535)
    client = start_client(server="www.bing.com", server_port=str(80), port=str(client_port))
    write_to(client, request)
    segments = read_segments_from(client)

    if not segments or len(segments) == 0:
        return False

    if len(segments) > 50:
        return True
    else:
        return False

# Tests to run.
TESTS = [
  # Test type, test name, test function
  ("basic", "Client sends data", client_sends,
   "Client has data to read from STDIN. Checks that a segment is sent as a\n" +
   "result, and that it has the right length."),
  ("basic", "Client receives data", client_receives,
   "Client 1 has data to read from STDIN. It should send a segment to\n" +
   "client 2. Checks that client 2 receives a segment of the right length."),
  ("basic", "Correct checksum", correct_checksum,
   "Client should send two segments. Checks that the checksums are correct."),
  ("basic", "Correct header fields", correct_header_fields,
   "Client 1 sends a segment to client 2. Checks that all header fields\n" +
   "are equal to what the reference would send."),
  ("basic", "Bidirectionally transfer data", bidirectional,
   "Client 1 sends data to client 2. Client 2 sends data to client 1.\n" +
   "Checks that both clients have received and outputted the correct data."),

  ("advanced", "Handles data larger than window size", large_data,
   "A really large string is placed in client 1's STDIN. Checks that all\n" +
   "the data is sent to client 2 and outputted."),
  ("advanced", "Handles segment corruption", segment_corruption,
   "Sends a corrupt segment from client 1 to client 2. Checks that client 2\n" +
   "eventually gets and outputs a correct segment."),
  ("advanced", "Handles segment drops", segment_drops,
   "Drops a segment from client 1 to client 2. Checks that client 2\n" +
   "eventually gets and outputs a correct segment."),
  ("advanced", "Handles segment delay", segment_delays,
   "Delays a segment from client 1 to client 2. Checks that client 2\n" +
   "eventually gets and outputs a correct segment."),
  ("advanced", "Handles duplicate segments", segment_duplicates,
   "Duplicates a segment from client 1 to client 2. Checks that client 2\n" +
   "eventually gets and outputs a correct segment."),
  ("advanced", "Handles truncated segments", segment_truncated,
   "Truncates a segment from client 1 to client 2. Checks that client 2\n" +
   "eventually gets and outputs a correct segment."),
  ("advanced", "Sends FIN when reading in EOF", fin_sent,
   "Puts an EOF in client 1's STDIN. Checks to see if client 2 sends a FIN."),
  ("advanced", "Tears down connection", connection_teardown,
   "Puts an EOF in client 1's and client 2's STDINs. Checks that connection\n" +
   "teardown happens on both sides (calls to ctcp_destroy())."),

#  ("hidden", "Ping pong short messages between two clients", ping_pong),
#  ("hidden", "Flow control when client stops reading data", flow_control),
#  ("hidden", "Handles reordered segments", reorders),
#  ("hidden", "Interoperation with reference", interoperation),
#  ("hidden", "No excessive retransmissions (5 total)", no_excessive_retrans),
#  ("hidden", "Ignores segments not within window", ignores_bad_seqno),
#  ("hidden", "Still sends data after receiving a FIN", send_after_fin),
#  ("hidden", "After reading EOF, can still receive segments", recv_after_eof)

  # Part 1b.
  ("advanced", "Handles sliding window", larger_windows,
   "Checks to see if sliding window is being used.\n"),
  ("advanced", "Talks to Google", ask_google,
   "Checks if we can get response from google.\n"),
  ("advanced", "Talks to Bing", ask_bing,
   "Checks if we can get response from bing.\n"),
  ("advanced", "Supports different send/receive windows",
   different_windows),
  ("advanced", "Window size field set in header", sets_window_size),
  ("advanced", "Supports multiple clients", multiple_clients),
]

################################# TESTER CODE ##################################

def run_tests(tests):
  """
  Function: run_tests
  -------------------
  Runs through all the specified tests.
  """
  global run_part1b, sliding_window_passed

  num_success = 0
  print "Starting tests..."
  print "\nResults"
  print "-------"

  # Go through each test.
  test_num = 0
  for i, test in enumerate(TESTS):
    # Skip ones not specified.
    if (i + 1) not in tests:
      continue

    # Print out test name.
    test_num += 1
    test_info = "  %d. %s" % (test_num, test[1])
    print test_info,

    # Do test and print out results.
    passed = False
    err = ""
    try:
      passed = test[2]()
      if passed:
        num_success += 1
    except KeyboardInterrupt:
      raise
    except IOError as e:
      err = "     |-> Possible segfault or early call to ctcp_destroy()"
    except IndexError as e:
      err = "     |-> Test failed but *may* pass if timeout is increased"
    except Exception as e:
      traceback.print_exc()
      pass
    print "." * (70 - len(test_info)),
    print "PASS" if passed else "FAIL"
    if len(err): print err
    teardown()

  # Automatic fail if no sliding window implemented.
  print "\nPASSED: %d/%d" % (num_success, len(tests))
  print "\nSCORE: %d/%d" % (num_success*10, len(tests)*10)
  if run_part1b and not sliding_window_passed and len(tests) == len(TESTS):
    print "AUTO_FAIL"


def print_test_list():
  """
  Function: print_test_list
  -------------------------
  Prints out the list of tests.
  """
  printed_advanced_header = False

  print "List of Tests Available\n-----------------------"
  for i, test in enumerate(TESTS):
    print "  %d. %s" % (i + 1, test[1])
    if len(test) > 3:
      for line in test[3].split("\n"):
        print "      " + len(str(i + 1)) * " " + line
      print ""
  print ""


def parse_args():
  """
  Function: parse_args
  --------------------
  Parse the tester arguments.

  Returns: List of test numbers to run.
  """
  global run_part1b

  parser = argparse.ArgumentParser()
  parser.add_argument("--tests", type=int, nargs="+", help="Tests to run")
  parser.add_argument("--list", action="store_const", const=True,
                      help="Lists all tests")
  parser.add_argument("--timeout", type=int, help="Tester timeout, in seconds")
  parser.add_argument("--part1b", action="store_const", const=True,
                      help="Run Part 1b tests")
  args = parser.parse_args()
  if not args.tests:
    # Filter out Lab 2 tests if not desired.
    args.tests = filter(lambda t: args.part1b or TESTS[t-1][0] != "part1b",
                        range(1, len(TESTS) + 1))
  TEST_TIMEOUT = args.timeout

  if args.part1b:
    run_part1b = True

  # Get the tests to run.
  test_nums = filter(lambda t: int(t) > 0 and int(t) <= len(TESTS), args.tests)
  if len(test_nums) < 1:
    print "Invalid test(s) specified. Tests range from 1 to %d." % len(TESTS)
    print_test_list()
    sys.exit(1)

  # Print out list of tests.
  elif args.list:
    print_test_list()
    sys.exit(0)

  return sorted(test_nums)


def verify():
  """
  Function: verify
  ----------------
  Make sure all the binaries exist for testing and user is running with sudo.
  """
  print "Making cTCP..."
  subprocess.call(["make", "clean"], stderr=PIPE, stdout=PIPE)
  result = subprocess.call(["make"])
  if result != 0:
    print "-" * 80
    print "ERROR: Could not make cTCP binary! Please fix compile errors."
    sys.exit(1)

  if not os.path.exists(CTCP_BINARY) or not os.path.exists(REFERENCE_BINARY):
    print "ERROR: ctcp and/or tester binary do not exist in the current " +\
          "directory!"
    sys.exit(1)

  # Check if running with sudo.
  if os.getenv("USER") != "root":
    print "ERROR: Must run this tester with sudo!"
    sys.exit(1)

  print "-" * 80

def teardown():
  """
  Function: teardown
  ------------------
  Test teardown.
  """
  subprocess.call(["killall", CTCP_BINARY], stderr=PIPE, stdout=PIPE)
  subprocess.call(["killall", REFERENCE_BINARY], stderr=PIPE, stdout=PIPE)


if __name__ == "__main__":
  teardown()
  tests_to_run = parse_args()
  verify()
  run_tests(tests_to_run)
