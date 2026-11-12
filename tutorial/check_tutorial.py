#!/usr/bin/env python3
"""Runs the MLE tutorial literally and checks every output it shows.

    check_tutorial.py [--record] [--keep] [--compose-dir DIR] [tutorial.md]

Every ```sh and ```sql block runs in order, against a fresh Docker Compose
project of its own (COMPOSE_PROJECT_NAME=mle-tutorial-check, port 33996), in
the directory of compose.yaml (docker/ in percona-mle). The ```text block that
follows a block (only blank lines between) is its expected output.

SQL blocks run in ONE client session, as for a reader who keeps the client
of step 1 open (`mysql -uapp -papp --table app` in the server container;
`connect` in a block reconnects, as in the interactive client); the client's
"at line N" in error messages is dropped (the interactive client does not
print it). Root blocks run in a session of their own (a second terminal).
HTML comments before a block (invisible in the rendered page):

    <!-- as: root -->          run the SQL block as root
    <!-- output: varies -->    run, but do not compare (timings, pids...)
    <!-- check: skip -->       do not run this block
    <!-- check-sh: CMD -->     run CMD here (checker only; e.g. a wait)

--record writes the actual outputs into the text blocks (as mtr --record);
review the diff. --record-varies also records the outputs marked "varies" (to
refresh the sample shown). --keep leaves the compose project running at the end.
Exit status 1 if any block failed or differed.
"""
import argparse
import difflib
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_COMPOSE_DIR = os.path.normpath(
    os.path.join(HERE, '../../percona-mle/docker'))
FENCE = re.compile(r'^```(\w*)[^\n]*\n(.*?)^```[ \t]*$', re.M | re.S)
DIRECTIVE = re.compile(r'<!--\s*([\w-]+)\s*:\s*(.*?)\s*-->')
NOISE = re.compile(r'^(mysql|mysqladmin): \[Warning\] Using a password.*\n', re.M)


def blocks(text):
    """(start, end, lang, body, directives, body_start, body_end) per fence."""
    out = []
    last_end = 0
    for m in FENCE.finditer(text):
        between = text[last_end:m.start()]
        # Directives: HTML comments after the previous block's end, in the
        # text right before this one.
        directives = DIRECTIVE.findall(between)
        out.append(dict(start=m.start(), end=m.end(), lang=m.group(1),
                        body=m.group(2), directives=directives,
                        gap=between, body_start=m.start(2), body_end=m.end(2)))
        last_end = m.end()
    return out


def run(cmd, cwd, stdin=None):
    p = subprocess.run(cmd, cwd=cwd, input=stdin, shell=isinstance(cmd, str),
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       text=True, errors='replace')
    return NOISE.sub('', p.stdout), p.returncode


class Session:
    """One mysql client session in the server container, fed block by block;
    the end of a block's output is found by a marker query."""

    def __init__(self, cwd, user, pw):
        # stderr is merged inside the container: docker exec carries stdout and
        # stderr as separate streams, which can arrive out of order.
        self.cmd = ['docker', 'compose', 'exec', '-T', 'mysql', 'sh', '-c',
                    'exec mysql -u%s -p%s --table --force --unbuffered --commands app 2>&1' % (user, pw)]
        self.cwd = cwd
        self.p = None
        self.n = 0

    def run(self, sql):
        if self.p is None or self.p.poll() is not None:
            self.p = subprocess.Popen(self.cmd, cwd=self.cwd, stdin=subprocess.PIPE,
                                      stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                      text=True, bufsize=1, errors='replace')
        self.n += 1
        mark = '@@END-OF-BLOCK-%d@@' % self.n
        self.p.stdin.write(sql.rstrip() + '\n' + "SELECT '%s' AS mark;\n" % mark)
        self.p.stdin.flush()
        lines = []
        while True:
            line = self.p.stdout.readline()
            if not line:  # the client ended
                return NOISE.sub('', ''.join(lines)), 1
            if mark in line:
                del lines[-3:]            # the marker table's top border and header
                self.p.stdout.readline()  # and its bottom border
                return NOISE.sub('', ''.join(lines)), 0
            lines.append(line)

    def close(self):
        if self.p and self.p.poll() is None:
            self.p.stdin.close()
            self.p.wait()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('tutorial', nargs='?', default=os.path.join(HERE, 'index.md'))
    ap.add_argument('--compose-dir', default=DEFAULT_COMPOSE_DIR)
    ap.add_argument('--record', action='store_true')
    ap.add_argument('--keep', action='store_true')
    ap.add_argument('--record-varies', action='store_true',
                    help='with --record: also record outputs marked "output: varies" (a sample)')
    a = ap.parse_args()
    os.environ.setdefault('COMPOSE_PROJECT_NAME', 'mle-tutorial-check')
    os.environ.setdefault('MLE_PORT', '33996')
    cwd = a.compose_dir
    text = open(a.tutorial).read()
    bl = blocks(text)
    run('docker compose --profile tools down -v', cwd)

    app = Session(cwd, 'app', 'app')
    failures = 0
    replacements = []  # (body_start, body_end, new body)
    i = 0
    while i < len(bl):
        b = bl[i]
        d = dict(b['directives'])
        for k, v in b['directives']:
            if k == 'check-sh':
                run(v, cwd)
        if b['lang'] not in ('sh', 'sql') or d.get('check') == 'skip':
            i += 1
            continue
        if b['lang'] == 'sh':
            actual, rc = run(b['body'], cwd)
        elif d.get('as') == 'root':
            root = Session(cwd, 'root', 'mle')
            actual, rc = root.run(b['body'])
            root.close()
        else:
            actual, rc = app.run(b['body'])
        if b['lang'] == 'sql':
            actual = re.sub(r'^(ERROR \d+ \(\w+\)) at line \d+:', r'\1:', actual, flags=re.M)
        line = text.count('\n', 0, b['start']) + 1
        first = b['body'].strip().splitlines()[0][:60] if b['body'].strip() else ''
        nxt = bl[i + 1] if i + 1 < len(bl) else None
        # The expected output: a text block right after (only blank lines and
        # directives in between).
        if nxt and nxt['lang'] == 'text' and DIRECTIVE.sub('', nxt['gap']).strip() == '':
            varies = d.get('output') == 'varies' or dict(nxt['directives']).get('output') == 'varies'
            want = nxt['body'].rstrip()
            got = '\n'.join(l.rstrip() for l in actual.rstrip().splitlines())
            if a.record and (not varies or a.record_varies):
                replacements.append((nxt['body_start'], nxt['body_end'], got + '\n' if got else ''))
            if varies:
                print(f'line {line}: ran (output varies) {first}')
            elif got == want:
                print(f'line {line}: ok {first}')
            else:
                failures += 1
                print(f'line {line}: DIFF {first}')
                sys.stdout.writelines(difflib.unified_diff(
                    [l + '\n' for l in want.splitlines()], [l + '\n' for l in got.splitlines()],
                    'tutorial', 'actual'))
            i += 2
            continue
        if actual.strip():
            print(f'line {line}: output not shown in the tutorial ({first}):')
            print(''.join('    ' + l + '\n' for l in actual.rstrip().splitlines()))
            if rc != 0:
                failures += 1
        else:
            print(f'line {line}: ok {first}')
        i += 1

    if a.record and replacements:
        for s, e, new in sorted(replacements, reverse=True):
            text = text[:s] + new + text[e:]
        open(a.tutorial, 'w').write(text)
        print(f'recorded {len(replacements)} outputs into {a.tutorial}')
    app.close()
    if not a.keep:
        run('docker compose --profile tools down -v', cwd)
    print('FAILED: %d block(s)' % failures if failures else 'all blocks ok')
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
