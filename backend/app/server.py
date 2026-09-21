"""Supported single-process server with pre-drain cancellation and a shutdown deadline."""
import argparse
import os
import threading

import uvicorn

DEFAULT_SHUTDOWN_SECONDS = 30


def shutdown_seconds():
    value = float(os.environ.get('PHLOX_SHUTDOWN_SECONDS', DEFAULT_SHUTDOWN_SECONDS))
    if not 1 <= value <= 300:
        raise ValueError('PHLOX_SHUTDOWN_SECONDS must be between 1 and 300 seconds')
    return value


class Server(uvicorn.Server):
    def handle_exit(self, sig, frame):
        # Repeated signals must not skip lifespan cleanup and release maintenance locks
        # while synchronous work is still alive. The watchdog provides the hard stop.
        self._captured_signals.append(sig)
        self.should_exit = True
        self._arm_deadline()

    def run(self, sockets=None):
        self.shutdown_finished = threading.Event()
        self.deadline_started = False
        try:
            super().run(sockets)
            if not self.started:
                raise SystemExit(3)
        finally:
            self.shutdown_finished.set()

    def _arm_deadline(self):
        if self.deadline_started:
            return
        self.deadline_started = True
        timeout = shutdown_seconds()
        finished = self.shutdown_finished

        def deadline():
            if not finished.wait(timeout):
                # Do not release maintenance locks while a live thread can still write.
                # Avoid logging locks here: a wedged thread may hold one. Never print payloads.
                os.write(2, b'Phlox shutdown_deadline_exceeded: forcing process exit; inspect interrupted work after restart.\n')
                os._exit(75)

        threading.Thread(target=deadline, name='phlox-shutdown-deadline', daemon=True).start()

    async def shutdown(self, sockets=None):
        from app import shutdown
        from app.observability import lifecycle
        from app.runs import worker
        from app.rag.jobs import worker as documents

        self._arm_deadline()
        shutdown.begin()
        worker.request_stop()
        documents.request_stop()
        lifecycle('shutdown_draining', reason='server_exit', deadline_seconds=shutdown_seconds())
        # Do not abandon ASGI tasks while their synchronous threads might still write.
        # The process watchdog bounds draining AND cleanup while maintenance stays locked.
        self.config.timeout_graceful_shutdown = None
        try:
            await super().shutdown(sockets)
        except BaseException:
            # A failed cleanup must not leave writers running after the lock is released.
            os.write(2, b'Phlox shutdown_cleanup_failed: forcing process exit; inspect interrupted work after restart.\n')
            os._exit(75)


def run(app='app.main:app', **options):
    shutdown_seconds()  # Reject an invalid deadline before opening a listener.
    config = uvicorn.Config(app, **{**options, 'workers': 1})
    server = Server(config)
    if config.should_reload:
        from uvicorn.supervisors import ChangeReload
        sock = config.bind_socket()
        try:
            ChangeReload(config, target=server.run, sockets=[sock]).run()
        finally:
            sock.close()
    else:
        server.run()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8000)
    args = parser.parse_args()
    run(host=args.host, port=args.port)


if __name__ == '__main__':
    main()
