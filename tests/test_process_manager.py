import sys
import time

from miner_supervisor.models import MinerConfig
from miner_supervisor.process_manager import ProcessManager


def test_process_manager_start_stop(tmp_path) -> None:
    config = MinerConfig(
        enabled=True,
        cwd=str(tmp_path),
        command=[sys.executable, "-c", "import time; time.sleep(5)"],
        env={},
        stdout_log_path=None,
        stderr_log_path=None,
    )
    manager = ProcessManager(config, "test-rig")
    pid = manager.start()
    assert pid is not None
    assert manager.is_running()
    time.sleep(0.1)
    manager.stop(timeout_seconds=1)
    assert not manager.is_running()
