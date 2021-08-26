# SPDX-License-Identifier: GPL-2.0
#
# Copyright (c) 2020 Facebook

from typing import Tuple
import email
import email.utils
import subprocess
import tempfile
import re

""" Test if relevant maintainers were CCed """

emailpat = re.compile(r'([^ <"]*@[^ >"]*)')

ignore_emails = {'linux-kernel@vger.kernel.org',   # Don't expect people to CC LKML on everything
                 'nipa@patchwork.hopto.org',       # For new files NIPA will get marked as committer
                 'jeffrey.t.kirsher@intel.com'}

# Maintainers who don't CC their co-employees
maintainers = {'michael.chan@broadcom.com': ['@broadcom.com'],
               'huangguangbin2@huawei.com': ['@huawei.com', '@hisilicon.com'],
               'anthony.l.nguyen@intel.com': ['@intel.com', '@lists.osuosl.org'],
               'saeed@kernel.org': ['@nvidia.com', '@mellanox.com']}


def cc_maintainers(tree, thing, result_dir) -> Tuple[int, str]:
    patch = thing

    msg = email.message_from_string(patch.raw_patch)
    addrs = msg.get_all('to', [])
    addrs += msg.get_all('cc', [])
    addrs += msg.get_all('from', [])
    addrs += msg.get_all('sender', [])
    included = set([e for n, e in email.utils.getaddresses(addrs)])

    ignore_domains = []
    sender_from = msg.get_all('from', ['nobody@nothing'])[0]
    match = emailpat.search(sender_from)
    if match:
        sender = match.group(1)
        if sender in maintainers:
            ignore_domains = maintainers[sender]

    expected = set()
    blamed = set()
    ignored = set()
    with tempfile.NamedTemporaryFile() as fp:
        patch.write_out(fp)
        command = ['./scripts/get_maintainer.pl', fp.name]
        with subprocess.Popen(command, cwd=tree.path, stdout=subprocess.PIPE) as p:
            line = p.stdout.readline().decode('utf8', 'replace')
            while line:
                match = emailpat.search(line)
                if match:
                    addr = match.group(1)
                    expected.add(addr)
                    if 'blamed_fixes' in line:
                        blamed.add(addr)
                for domain in ignore_domains:
                    if domain in addr:
                        ignored.add(addr)
                line = p.stdout.readline().decode('utf8', 'replace')
            p.wait()

    expected.difference_update(ignore_emails)
    blamed.difference_update(ignore_emails)

    expected.difference_update(ignored)
    blamed.difference_update(ignored)

    found = expected.intersection(included)
    missing = expected.difference(included)
    missing_blamed = blamed.difference(included)
    if len(missing_blamed):
        return 1, f"{len(missing_blamed)} blamed authors not CCed: {' '.join(missing_blamed)}; " + \
                  f"{len(missing)} maintainers not CCed: {' '.join(missing)}"
    if len(missing):
        ret = 250 if len(found) > 1 else 1
        return ret, f"{len(missing)} maintainers not CCed: {' '.join(missing)}"
    return 0, f"CCed {len(found)} of {len(expected)} maintainers"
