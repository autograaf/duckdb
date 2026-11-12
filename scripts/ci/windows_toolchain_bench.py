import argparse
import csv
import glob
import os
import statistics
import subprocess
import sys

CONFIGS = ['a', 'b', 'c']
QUERIES = list(range(1, 23))


def sql_path(path):
    return os.path.abspath(path).replace('\\', '/')


def run_cli(exe, database, sql, log_path, readonly=False):
    args = [exe, '-unsigned']
    if readonly:
        args.append('-readonly')
    args.append(database)
    proc = subprocess.run(args, input=sql, capture_output=True, text=True, timeout=3600)
    with open(log_path, 'a', encoding='utf-8') as log:
        log.write(proc.stdout)
        log.write(proc.stderr)
    if proc.returncode != 0:
        sys.stderr.write(proc.stdout[-4000:] + proc.stderr[-4000:])
        raise RuntimeError(f'{exe} exited with {proc.returncode}')
    return proc.stdout


def session_sql(extension, marks_csv):
    lines = [
        f"LOAD '{extension}';",
        "ATTACH ':memory:' AS marks;",
        'CREATE TABLE marks.m (pass INTEGER, query INTEGER, phase VARCHAR, ts TIMESTAMPTZ);',
        '.timer on',
    ]
    # pass 0 only fills the buffer pool, since every session starts a new process
    for pass_nr in (0, 1):
        for query in QUERIES:
            lines.append(f"INSERT INTO marks.m VALUES ({pass_nr}, {query}, 'start', now());")
            lines.append(f'PRAGMA tpch({query});')
            lines.append(f"INSERT INTO marks.m VALUES ({pass_nr}, {query}, 'end', now());")
    lines.append('.timer off')
    lines.append(
        f"COPY (SELECT pass, query, phase, epoch_us(ts) AS us FROM marks.m) TO '{marks_csv}' (HEADER, DELIMITER ',');"
    )
    return '\n'.join(lines) + '\n'


def read_marks(marks_csv):
    marks = {}
    with open(marks_csv, newline='') as f:
        for row in csv.DictReader(f):
            marks[(int(row['pass']), int(row['query']), row['phase'])] = int(row['us'])
    return {q: (marks[(1, q, 'end')] - marks[(1, q, 'start')]) / 1e6 for q in QUERIES}


def spread(values):
    return (max(values) - min(values)) / statistics.median(values)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--builds', required=True)
    parser.add_argument('--out', required=True)
    parser.add_argument('--rounds', type=int, default=5)
    args = parser.parse_args()
    os.makedirs(args.out, exist_ok=True)

    builds = {}
    for config in CONFIGS:
        root = os.path.join(args.builds, f'toolchain-bench-{config}')
        exe = os.path.abspath(os.path.join(root, 'duckdb.exe'))
        extensions = glob.glob(os.path.join(root, 'repository', '*', '*', 'tpch.duckdb_extension'))
        if len(extensions) != 1:
            raise RuntimeError(f'config {config}: expected one tpch extension, found {extensions}')
        database = os.path.abspath(os.path.join(args.out, f'tpch-{config}.duckdb'))
        log_path = os.path.join(args.out, f'log-{config}.txt')
        builds[config] = (exe, sql_path(extensions[0]), database, log_path)
        setup = (
            f"LOAD '{sql_path(extensions[0])}';\n"
            'PRAGMA version;\nPRAGMA platform;\n'
            "SELECT current_setting('threads') AS threads;\n"
            "SELECT extension_name, loaded, install_mode, extension_version FROM duckdb_extensions() "
            "WHERE extension_name = 'tpch';\n"
            'CALL dbgen(sf = 1);\nCHECKPOINT;\n'
        )
        print(f'== config {config}: {exe}\n   extension {extensions[0]}')
        print(run_cli(exe, database, setup, log_path), flush=True)

    times = {config: {q: [] for q in QUERIES} for config in CONFIGS}
    for round_nr in range(args.rounds):
        # rotating the order keeps one config from always running first
        order = CONFIGS[round_nr % len(CONFIGS) :] + CONFIGS[: round_nr % len(CONFIGS)]
        for config in order:
            exe, extension, database, log_path = builds[config]
            marks_csv = sql_path(os.path.join(args.out, f'marks-{config}-{round_nr}.csv'))
            run_cli(exe, database, session_sql(extension, marks_csv), log_path, readonly=True)
            result = read_marks(marks_csv)
            for q in QUERIES:
                times[config][q].append(result[q])
            print(f'round {round_nr} config {config}: total {sum(result.values()):.3f}s', flush=True)

    with open(os.path.join(args.out, 'timings.csv'), 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(['config', 'query', 'round', 'seconds'])
        for config in CONFIGS:
            for q in QUERIES:
                for round_nr, seconds in enumerate(times[config][q]):
                    writer.writerow([config, q, round_nr, f'{seconds:.6f}'])

    out = ['| Query | ' + ' | '.join(f'{c.upper()} median s | {c.upper()} spread' for c in CONFIGS) + ' |']
    out.append('|---|' + '---|---|' * len(CONFIGS))
    for q in QUERIES:
        cells = []
        for config in CONFIGS:
            values = times[config][q]
            cells.append(f'{statistics.median(values):.4f} | {spread(values) * 100:.1f}%')
        out.append(f'| Q{q:02d} | ' + ' | '.join(cells) + ' |')
    cells = []
    for config in CONFIGS:
        totals = [sum(times[config][q][r] for q in QUERIES) for r in range(args.rounds)]
        cells.append(f'{statistics.median(totals):.4f} | {spread(totals) * 100:.1f}%')
    out.append('| Total per round | ' + ' | '.join(cells) + ' |')
    cells = [f'{sum(statistics.median(times[c][q]) for q in QUERIES):.4f} | ' for c in CONFIGS]
    out.append('| Sum of medians | ' + ' | '.join(cells) + ' |')
    summary = '\n'.join(out) + '\n'
    print(summary)
    with open(os.path.join(args.out, 'summary.md'), 'w') as f:
        f.write(summary)
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as f:
            f.write(summary)


if __name__ == '__main__':
    main()
