===============
NIPA contest HW
===============

This documentation covers the NIPA HW testing infra (which is deployed on
machines owned by netdev foundation).

This diagram shows the layout of the services within netdev foundation::

  -----------------    ---------------    ------------------
  |  control node |    | build node  |    |    Machine 1   |
  |   _________   |    |   ________  |    |  ___________   |
  |  / machine \  |    |  / hwksft \ |    | / hw-worker \  |
  |  \_control_/  |    |  \__NIC0__/ |    | \___________/  |
  |       ||      |    |   ________  |    |                |
  |   ____\/__    |    |  / hwksft \ |    | ======  ====== |
  |  /   DB   \   |    |  \__NIC1__/ |    | |NIC0|  |NIC1| |
  |  \________/   |    |   ________  |    |_||==||__||==||_|
  |_______________|    |  / hwksft \ |       \__/   /
                       |  \__NIC2__/ |             /
                       |_____________|        ____/
                                             /
                                           -||==||-----------
                                           | |NIC2|         |
                                           | ======         |
                                           |     Machine 2  |
                                           |  ___________   |
                                           | / hw-worker \  |
                                           | \___________/  |
                                           |________________|

Database
========

The HW system uses the following tables in the database.

machine_info
------------

Metadata about machines for NIC testing.

 - ID, auto int, pkey
 - name, varchar 128
 - mgmt ipaddr

machine_info_sec
----------------

Separate table with sensitive information about machines.

 - BMC ipaddr [sec]
 - BMC pass [sec]

nic_info
--------

Metadata about NICs in the test machines.

 - ID, auto int, pkey
 - machine ID, fkey (machine_info)
 - vendor, varchar 128
 - model, varchar 128
 - peer ID, fkey int (nic_info)
 - ifname, varchar 128
 - ip4addr, varchar 128
 - ip6addr, varchar 128

SOL
---

Serial logs storage.

 - ID, auto int, pkey
 - machine ID
 - ts, timestamp, usec
 - line, varchar 200
 - eol, bool

Lines longer than 200 will be broken up, eol is true on last chunk.

reservations
------------

Machine reservations.

 - ID, auto int, pkey
 - ts_start, timestamp, sec
 - ts_end, timestamp, sec
 - status, enum: ACTIVE, CLOSED, TIMEDOUT
 - metadata, text, flexible JSON-formatted metadata

reservation_machines
--------------------

Join table mapping reservations to machines. Needed to support atomic
multi-machine reservations (e.g. NIC + peer on different machines).

 - reservation_id, fkey (reservations)
 - machine_id, fkey (machine_info)

Machine control
===============

Service responsible for reserving machines and controlling them over BMC.
It uses ipmitool for most commands, and UDP socket to receive Serial-over-LAN.
The machine info is read from the ``machine_info`` table.

Each API endpoint takes a ``caller`` parameter to attribute calls to other
services for debug.

Service prints logs to stdout (available via journalctl).

APIs
----

get_machine_info
~~~~~~~~~~~~~~~~

Shows public info about the machine (from ``machine_info``,
_not_ ``machine_info_sec``).

This endpoint also adds to the output information about reservation.
Either who (``caller``) currently has the machine reserved, and when
that reservation started. Or if not reserved who was the last one
to reserve and when the reservation ended.

get_nic_info
~~~~~~~~~~~~

Shows info from ``nic_info``.

get_sol_logs
~~~~~~~~~~~~

Query logs. The endpoint reconstructs the full log lines using the ``eol``
field. The chunking is therefore not visible to the querier.

The querier is expected to pass in ``start_id`` to fetch logs from a specific
starting point. If no ``start_id`` is specified first / last set of lines
will be fetched, depending on ``sort``.

Each query returns ``last_id`` which lets querier ask for next lines.
Output does not contain IDs per line because if line is constructed from
multiple DB rows there would be multiple IDs per line.

in:
 - caller
 - machine_id
 - start_id, ID of the log line querier already seen
 - limit, number of log lines to fetch
 - sort, optional sort order (desc or asc), default asc
out:
 - machine_id
 - last_id
 - array of:
   - ts
   - line

power_cycle
~~~~~~~~~~~

Power cycle the machine using BMC.

This endpoint doesn't currently have any security checks.
Callers are trusted to never power cycle machines they don't have reserved.

in:
 - caller
 - machine_id
out:
 - code, int, 0 on success or non-zero on failure
 - status, string, "success" or error information

reserve
~~~~~~~

Reserve a group of machines. The reservation is atomic (all machines or none).
``timeout`` parameter sets the min refresh rate expected via the
``reservation_refresh`` endpoint.

``reservation_id`` will be present in the output only if reservation succeeded.
Otherwise caller should try again later. (Service does not maintain a queue
of outstanding waiters).

in:
 - caller
 - array of:
   - machine_id
out:
 - timeout
 - reservation_id

reservation_refresh
~~~~~~~~~~~~~~~~~~~

Refresh reservation. Each reservation automatically times out if not refreshed.

If refresh fails the caller must abort and stop touching the machine. Someone
else may own it.

in:
 - caller
 - reservation_id

reservation_close
~~~~~~~~~~~~~~~~~

Release machines in a reservation immediately.

in:
 - caller
 - reservation_id

Config
------

 - reservation timeout, seconds

CLI
---

The ``nipa-mctrl`` CLI (``/usr/local/bin/nipa-mctrl`` on ctrl) provides
command-line access to the machine_control API::

  nipa-mctrl machines            # list machines and health state
  nipa-mctrl nics                # list NICs
  nipa-mctrl sol --machine-id 1  # view SOL logs
  nipa-mctrl reserve --machine-ids 1,2  # reserve machines
  nipa-mctrl close --reservation-id 5   # release a reservation
  nipa-mctrl power-cycle --machine-id 1 # power cycle via BMC

Add ``--json`` for machine-parseable output. Defaults to
``http://localhost:5050``; override with ``--url`` or ``MC_URL`` env var.

In-memory state
---------------

machine_state
~~~~~~~~~~~~~

If the machine is not reserved service SSHs to it every 5min and
checks uptime and kernel version. If SSH fails machine state progresses
HEALTHY -> MISS_ONE -> MISS_TWO. After three missed checks the machine
is power-cycled via BMC (POWER_CYCLE_ISSUED). If the machine is still
down after power cycle, the miss counter restarts (MISS_ONE) and the
cycle repeats.

If the machine is RESERVED the machine state is just tracking last
refresh time to potentially time out the reservation.

Only machines in HEALTHY state can be reserved. If someone tries
to reserve a machine not in HEALTHY state we respond with try again.

Refreshed every 5min, and immediately after reservation is released.

 - machine ID
 - state, enum: RESERVED, HEALTHY, MISS_ONE, MISS_TWO, POWER_CYCLE_ISSUED
 - last_reservation: CLOSED, TIMEOUT
 - reservation_last_refresh
 - uptime
 - kernel version

Operation
---------

This service has three main responsibilities:
 1. collecting SOL logs
 2. managing reservations
 3. controlling the machines

The service discovers all machines using the ``machine_info`` table at startup.

SOL collection
~~~~~~~~~~~~~~

At startup the service spawns a persistent ``ipmitool sol activate``
session for each machine (using BMC credentials from ``machine_info_sec``).
Each session runs in its own thread, reading stdout and inserting lines
into the ``sol`` table. If a session drops it is automatically
reconnected after a short delay. Stale sessions are deactivated before
each new connection attempt.

Managing reservations
~~~~~~~~~~~~~~~~~~~~~

At startup service scans the ``reservations`` table to see if there are
any open reservations. If there are it adds them to the in-memory state.
The entries added from the SQL table at startup have the "last refresh"
time of "now" to give active owner time to ping us before timing out
the reservation.

After initialization the service listens for new reservations and
also counts down the timeouts. Reservation timeout should be read from
the config, the reservation owner should be told refresh time is half
of the reservation timeout.

When reservation closes or times out machine should be rebooted (via SSH,
and BMC if SSH fails).

Controlling machines
~~~~~~~~~~~~~~~~~~~~

This is a bit of a meta endpoint. For now it only allows the callers
to power cycle the machine via BMC in case SSH connectivity is lost.

hwksft
======

Testing service. There are two instances of this service per NIC.
One for "normal" kernel build and one for a debug kernel build.

Unlike ``vmksft-p`` is more of a management / orchestration service.
The task of executing the tests is delegated to ``hw-worker``.

Config
------

 - NIC ID to test against
 - information about the branch stream to follow (like vmksft-p.py)
 - stability endpoint URL and the remote name used by the results collector
 - path to extra kernel config (incl. the driver for the NIC in question)
 - reservation retry time (seconds)
 - max kexec boot timeout (seconds)
 - max power cycle timeout (seconds), how long to wait for the machine to
   come back after an SSH reboot or a BMC power cycle
 - max test time (seconds), per kexec attempt
 - max total test time (seconds), across *all* attempts of a run including
   the reboots between them; caps how long one run can hold a reservation
 - test timeout (seconds), per-test wall-clock limit enforced by
   ``hw-worker``; passed to the DUT in ``nic-test.env``, defaults to the
   runner's own value when unset
 - max crash retries, how many times a run may be resumed after a kernel
   crash before giving up on the remaining tests
 - max timeout reboots, the same budget for tests which died with their own
   ``subprocess.TimeoutExpired`` (see `Test timeout recovery`_).  Tracked
   separately from crash retries so a couple of flaky timeouts cannot eat
   the crash budget and silently drop the tail of the suite
 - crash wait time (seconds), how long to wait after a crash is detected
   in SOL logs and no new SOL output before power cycling
 - SOL poll interval (seconds), how often to check SOL logs for crashes

Operation
---------

Upon detection of a new testing branch (each step may fail, of course):

1. Build the kernel and ksft
2. Resolve which machines we need to reserve for the NIC ID - machine in which
   the NIC resides and the machine in which peer NIC resides if the peer ID
   is not the same as NIC ID (loopback)
3. Keep trying to reserve the machines.
   Note that reservation refresh calls are placed explicitly rather than
   handled by a separate thread to avoid hung runners from keeping the machine.
4. Deploy the test artifacts (kernel and ksft bundle) under
   ``/srv/hw-worker/tests/$reservation_id``
   Test artefacts must include correct config file for NIPA HW test runner
   (NETIF, LOCAL_V4, LOCAL_V6, REMOTE_V4, REMOTE_V6, REMOTE_TYPE, etc).
   If stability is configured, hwksft fetches entries for its remote, filters
   them to currently ignored subtests already on a failure streak for its
   executor, and deploys the compact result as ``known-bad.json``. The worker
   skips a failed test's retry only when every failed nested subtest appears in
   that file.
5. kexec the machine into the newly deployed kernel
6. Wait for machine to come back, and ``nipa-hw-worker`` service to exit,
   while refreshing the reservation. During this wait hwksft monitors
   SOL logs for kernel crashes (see `Crash recovery`_).
   When the service exits hwksft checks its journal for a reboot request.
   ``hw-worker`` asks for one after a kernel crash, and after a test dies
   with its own ``subprocess.TimeoutExpired`` (see
   `Test timeout recovery`_).  Either way hwksft reboots the machine and
   goes back to step 5, so the rest of the suite runs on a clean system.
7. Copy back the outputs from ``/srv/hw-worker/results/$reservation_id/``
   into appropriate locations in local FS (again, mimicking the ``vmksft-p``
   layout if outputs and json files in separate directories).
   Tests that appear in ``.attempted`` but not in ``results.json`` are
   reported as failures with crash info.
8. Release the reservation.
9. Include the result entry in the manifest file and wait for next branch.

Crash recovery
--------------

When a test causes a kernel crash the machine may become unresponsive.
hwksft detects this and recovers automatically so remaining tests can
continue.

Detection: hwksft polls ``get_sol_logs`` from ``machine_control`` at
``sol_poll_interval`` (default 15s). If the SOL output contains crash
markers (``RIP:``, ``Call Trace:``, ``ref_tracker:``, or
``unreferenced object``) a crash is flagged.

After a crash is flagged there are two paths:

Self-reboot: if subsequent SOL output contains ``[    0.000000]`` (the
first line of a kernel boot log), the machine is already rebooting itself
(e.g. due to ``panic=N`` kernel parameter). In this case the power cycle
step is skipped — hwksft proceeds directly to waiting for SSH and
continuing the recovery sequence below.

Hung machine: if no new SOL output appears for ``crash_wait_time`` (default
120s) after the crash, the machine is assumed hung. hwksft power-cycles it
via the ``machine_control`` ``power_cycle`` API.

Recovery sequence (after self-reboot or power cycle):

1. Wait for SSH to become available (machine boots into default kernel).
2. kexec into the test kernel again.
3. hw-worker starts, checks ``.kernel-version`` against ``uname -r``.
   On the default kernel the version won't match — hw-worker exits.
   After kexec into the test kernel the version matches — hw-worker
   resumes testing. Tests listed in ``.attempted`` are skipped
   (they caused the crash) and recorded as failures.
4. hwksft continues the wait loop from step 6 above.

This cycle can repeat multiple times if different tests cause different
crashes. Each crash skips only the offending test.

Test timeout recovery
---------------------

Many ``drivers/net`` selftests are Python and shell out with
``subprocess.run(..., timeout=N)``.  When one of those inner commands hangs
the test dies with an uncaught ``subprocess.TimeoutExpired`` traceback, so it
never runs its cleanup / ``__exit__``: netns, interfaces, XDP programs, qdiscs
and sysctls are left behind and the machine is in an unknown state.  Anything
run after that point is suspect.

Note this is *not* the same as ``hw-worker`` hitting its own per-test
wall-clock limit (``test_timeout``).  There the test tree is torn down
deliberately -- SIGINT first, so cleanup handlers run -- the output is marked
``NIPA RUNNER TIMEOUT``, and the retry is still worth doing.  Only the test's
own traceback triggers the recovery below.

Detection: after each test (and after its retry, if one ran) ``hw-worker``
scans the saved ``stdout`` and ``stderr`` for a line starting with
``subprocess.TimeoutExpired``, optionally behind kselftest's ``# `` TAP
prefix.  Both streams are checked because whether the traceback reaches stdout
depends on how kselftest's ``runner.sh`` merges them.

Recovery sequence:

1. ``hw-worker`` records ``warnings`` in the test's ``info`` file, skips the
   test's retry (retrying on a dirty machine is worthless and may hang the
   same way), stops the run, and prints
   ``NIPA DETECTED TEST TIMEOUT, REBOOT ME PLEASE`` before exiting.
   The reboot is always driven by hwksft, never by the DUT itself.
2. hwksft sees the service exit, greps the journal, and reboots the machine
   with ``reboot_machine()`` -- SSH ``reboot`` first, falling back to a BMC
   power cycle only if SSH is unresponsive or the machine does not come back.
   After a mere test timeout SSH almost always works and is much faster.
3. hwksft kexecs into the test kernel again and ``hw-worker`` resumes.  The
   offending test is in ``.attempted``, so it is skipped.
4. Repeats up to ``max timeout reboots`` times, then hwksft gives up on the
   remaining tests (it still performs the final reboot).

The timed-out test is reported with its real result -- normally ``fail`` --
and is *not* retried, so it can never be reported as a flake.  Tests which
ran before it keep their results; there is no tainting, because the machine
is rebooted immediately rather than being used for further tests.

State files on the test machine
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The following files under ``/srv/hw-worker/tests/$reservation_id/``
coordinate crash recovery between hwksft and hw-worker:

``.kernel-version``
  Written by hwksft at deploy time. Contains the output of
  ``make kernelversion``. hw-worker compares this against ``uname -r``
  to verify it booted the test kernel. Mismatched version means wrong
  kernel — exit without running tests.

``.attempted``
  JSON list of test names (``target/prog``) already attempted. Written
  with ``fsync`` **before** each test starts. On resume after a crash,
  tests in this list are skipped. This ensures a crashing test is never
  retried.

  A test which is in this list but produced no ``info`` file never got to
  finish -- it is synthesised as a failure with crash info. A test stopped
  for a *timeout* did write its ``info`` file before ``hw-worker`` exited,
  so it is reported with its real result plus a ``warnings`` entry.

hw-worker
=========

NIPA's ``hw-worker`` is the actual service that executes the tests.
It's one-shot, on-boot service, so it should see that there are tests
that need to be run after kexec completes boot.

Operation
---------

1. Scan ``/srv/hw-worker/tests`` for outstanding tests. Only the newest one
   will be executed.
2. Read ``.kernel-version`` from the test directory and compare against
   ``uname -r``. If the running kernel doesn't match, this is a boot into
   the wrong kernel (e.g. default kernel after power cycle) — exit
   immediately.
3. Open ``/dev/kmsg`` and drain existing boot messages to
   ``results_dir/boot-dmesg``.
4. Run the tests. For each test:
    a. Check if test name is in ``.attempted`` — if so, skip (crash recovery).
    b. Write test name to ``.attempted`` + fsync before execution.
    c. Run via ``./run_kselftest.sh -t <target>:<test>`` (installed form).
    d. Capture stdout/stderr, save to ``results_dir/<idx>-<name>/``.
    e. Drain ``/dev/kmsg`` — if any dmesg output was produced during
       the test, save it to ``results_dir/<idx>-<name>/dmesg``.
    f. Scan the saved stdout/stderr for the test's own
       ``subprocess.TimeoutExpired``. If found, skip the retry, record a
       ``warnings`` entry and stop the run — the machine needs a reboot
       (see `Test timeout recovery`_).
    g. Save metadata to ``results_dir/<idx>-<name>/info`` (JSON).
5. Results are saved under ``/srv/hw-worker/results/$reservation_id/``.
   hw-worker does **not** determine pass/fail — that is done by hwksft
   when it copies back and parses the output files.
6. Print a sentinel asking to be rebooted if a kernel crash
   (``NIPA DETECTED SYSTEM CRASH, REBOOT ME PLEASE``) or a test timeout
   (``NIPA DETECTED TEST TIMEOUT, REBOOT ME PLEASE``) was seen. This is the
   only channel hw-worker has to the orchestrator.
7. Service exits.

Output artifacts
----------------

hw-worker produces the following files under
``/srv/hw-worker/results/$reservation_id/``.  hwksft copies this tree
back and parses it to build the final result JSON.

::

  $reservation_id/
  ├── boot-dmesg                    # dmesg from boot until first test
  ├── 0-test_name/                  # per-test output directory
  │   ├── stdout                    # test stdout (KTAP/TAP output)
  │   ├── stderr                    # test stderr
  │   ├── info                      # JSON: {retcode, time, target, prog}
  │   └── dmesg                     # dmesg during this test (if any)
  ├── 1-another_test/
  │   ├── stdout
  │   ├── stderr
  │   ├── info
  │   └── dmesg
  └── ...

``info`` JSON fields:

``retcode``
  Exit code of ``run_kselftest.sh``.  0 = pass, 4 = skip, other = fail.

``time``
  Wall-clock seconds the test took (float).

``target``
  kselftest collection name (e.g. ``drivers/net/hw``).

``prog``
  Test program name within the collection (e.g. ``rss_drv.py``).

``retry_retcode``
  Exit code of the retry run, present only if the test was retried.

``crashes``
  List of crash fingerprints extracted from the test's dmesg, if any.

``warnings``
  List of strings describing something which went wrong with the *machine*
  rather than the test, e.g. a test hitting ``subprocess.TimeoutExpired``.
  Purely informational — it does not change the test's result. hwksft copies
  this onto the test's result entry and also aggregates all of a run's
  warnings into a run-level ``warnings`` array, which is what the
  ``status.html`` summary shows.
