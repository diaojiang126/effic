"""轻量级运行时指标：进程内滚动统计识别各阶段的耗时与失败率。

不引入额外依赖、不落盘，重启后清零。用于 /api/metrics 查看 P50/P95 与失败率，
定位"慢在哪一段、失败率多高"。
"""
import threading
import time
from collections import deque


def _percentile(sorted_values, ratio):
    if not sorted_values:
        return 0.0
    index = min(len(sorted_values) - 1, int(len(sorted_values) * ratio))
    return sorted_values[index]


class _StageMetrics:
    def __init__(self, max_samples):
        self._durations = deque(maxlen=max_samples)
        self.total = 0
        self.failures = 0

    def record(self, elapsed_ms, success):
        self.total += 1
        if not success:
            self.failures += 1
        self._durations.append(elapsed_ms)

    def snapshot(self):
        values = sorted(self._durations)
        return {
            'count': self.total,
            'failures': self.failures,
            'failure_rate': round(self.failures / self.total, 4) if self.total else 0.0,
            'p50_ms': round(_percentile(values, 0.50), 1),
            'p95_ms': round(_percentile(values, 0.95), 1),
            'max_ms': round(values[-1], 1) if values else 0.0,
        }


class _Timer:
    def __init__(self, metrics, stage):
        self._metrics = metrics
        self._stage = stage
        self._started = 0.0

    def __enter__(self):
        self._started = time.perf_counter()
        return self

    def __exit__(self, exc_type, exc, traceback):
        elapsed_ms = (time.perf_counter() - self._started) * 1000
        self._metrics.record(self._stage, elapsed_ms, success=exc_type is None)
        return False


class Metrics:
    def __init__(self, max_samples=500):
        self._lock = threading.Lock()
        self._stages = {}
        self._max_samples = max_samples
        self._started_at = time.time()

    def record(self, stage, elapsed_ms, success=True):
        with self._lock:
            stage_metrics = self._stages.get(stage)
            if stage_metrics is None:
                stage_metrics = _StageMetrics(self._max_samples)
                self._stages[stage] = stage_metrics
            stage_metrics.record(elapsed_ms, success)

    def timeit(self, stage):
        return _Timer(self, stage)

    def snapshot(self):
        with self._lock:
            return {
                'uptime_seconds': int(time.time() - self._started_at),
                'stages': {name: item.snapshot() for name, item in sorted(self._stages.items())},
            }


METRICS = Metrics()
