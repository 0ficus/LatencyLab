"""Exercise the real syscall path; works on hosts both with and without PMU access."""
import json
import subprocess
import sys

probe = sys.argv[1]

def run(*args):
    return subprocess.run([probe, *args], capture_output=True, text=True, timeout=30)

p = run('--iterations', '1000000')
assert p.returncode == 0, p.stderr
result = json.loads(p.stdout)
assert result['schema_version'] == 1
assert result['elapsed_ns'] > 0
assert result['iterations'] == 1000000
expected = 0x123456789abcdef
for i in range(1000000):
    expected = (expected * 6364136223846793005 + i + 1442695040888963407) % (1 << 64)
assert result['checksum'] == expected
assert set(result['metrics']) == {'cycles', 'instructions', 'cache_misses', 'task_clock'}
for metric in result['metrics'].values():
    if metric['status'] == 'available':
        assert metric['value'] >= 0
        assert 0 < metric['time_running_ns'] <= metric['time_enabled_ns']
        scaled = metric['raw_value'] * metric['time_enabled_ns'] / metric['time_running_ns']
        assert abs(metric['value'] - scaled) <= max(1e-6, abs(scaled) * 1e-12)
        assert metric['reason'] is None
    else:
        assert metric['status'] == 'unavailable'
        assert metric['value'] is None and metric['raw_value'] is None
        assert metric['reason']

strict = run('--iterations', '1000000', '--require-hardware')
strict_result = json.loads(strict.stdout)
assert strict.returncode == (0 if strict_result['hardware_counters_available'] == 3 else 2)
for args in [('--cpu', '-1'), ('--cpu', '99999'), ('--iterations', '0'),
             ('--iterations', 'x'), ('--iterations', '2x'), ('--iterations',), ('--unknown',)]:
    p = run(*args)
    assert p.returncode == 1 and p.stderr and not p.stdout, (args, p)
print('Real syscall, JSON, checksum, scaling, strict mode and invalid arguments: OK')
