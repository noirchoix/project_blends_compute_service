from __future__ import annotations

import argparse
import os
import socket
import time

from project_blends_compute.quantum.service import QuantumService
from project_blends_compute.settings import Settings


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the persistent Project Blends quantum worker")
    parser.add_argument("--once", action="store_true", help="Process at most one job")
    parser.add_argument("--poll-seconds", type=float, default=None)
    args = parser.parse_args()
    settings = Settings()
    settings.ensure_runtime_dirs()
    service = QuantumService(settings)
    worker_id = f"{socket.gethostname()}:{os.getpid()}"
    poll = args.poll_seconds or settings.quantum_worker_poll_s
    service.queue.recover_stale(settings.quantum_stale_after_minutes)
    while True:
        job = service.queue.claim(worker_id, job_type="quantum")
        if job is None:
            if args.once:
                return
            time.sleep(poll)
            continue
        service.run_job(job, worker_id)
        if args.once:
            return


if __name__ == "__main__":
    main()
