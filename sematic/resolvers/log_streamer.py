# Standard Library
import contextlib
import os
import signal
import stat
import sys
import time
import traceback
from typing import Callable, Optional

# Sematic
from sematic.config import KUBERNETES_POD_NAME_ENV_VAR
from sematic.storage import S3Storage
from sematic.utils.retry import retry
from sematic.utils.stdout import redirect_to_file_descriptor

"""
An overview of how logging works:
- stdout and stderr are redirected to a file from the Sematic worker
- the Sematic worker on launch starts a child process that periodically
    uses our storage abstraction to upload the log file to persistent storage
- the name of the logs on the remote contain metadata about what run and job type
   the logs came from
- the server reads the logs from persistent storage for UI display
- it's safe to assume that the worker will have write access to the persistent storage
  because it's the same bucket used for artifacts.
"""


DEFAULT_LOG_UPLOAD_INTERVAL_SECONDS = 10
_LAST_NON_EMPTY_DELTA_TEMPLATE = "{}.previous"


def _flush_to_file(file_path, read_handle, uploader, remote_prefix, timeout=None):
    if os.path.exists(file_path) and os.stat(file_path)[stat.ST_SIZE] > 0:
        # save the last non-empty delta file somewhere for tailing
        os.rename(file_path, _LAST_NON_EMPTY_DELTA_TEMPLATE.format(file_path))
    started_reading = time.time()
    # Use w+ mode; should overwrite whatever was in the prior delta file
    with open(file_path, "w+") as fp:
        while timeout is None or time.time() - started_reading < timeout:
            line = read_handle.readline()
            if len(line) > 0:
                # The line would at least have the newline char if it was a blank.
                # hence the "if"
                fp.write(line)
            else:
                break  # no more to read right now; go ahead and flush
            fp.flush()
    uploader(file_path, remote_prefix)


def _stream_logs_to_remote_from_file_descriptor(
    file_path: str,
    read_from_file_descriptor: int,
    upload_interval_seconds: int,
    remote_prefix: str,
    uploader: Callable[[str, str], None],
):
    """Execute infinite loop to periodically upload from file_path to remote storage

    Parameters
    ----------
    file_path:
        The path to the local file that's being uploaded
    read_from_file_descriptor:
        The file descriptor that's being read from.
    upload_interval_seconds:
        The amount of time between the end of one upload and the start of the next
    remote_prefix:
        The prefix for the remote storage location where the log files will be kept.
        The actual file name will be unique for each upload, increasing monoatonically
        with time
    uploader:
        A callable to perform the upload. It will be given the path to upload from and
        the remote prefix as arguments.
    """
    read_handle = os.fdopen(read_from_file_descriptor)

    def do_exit(signal_num, frame):
        # unregister so we don't do this multiple times
        signal.signal(signal.SIGTERM, lambda *_, **__: None)
        _flush_to_file(file_path, read_handle, uploader, remote_prefix)
        os._exit(0)

    signal.signal(signal.SIGTERM, do_exit)
    while True:
        _flush_to_file(
            file_path,
            read_handle,
            uploader,
            remote_prefix,
            timeout=upload_interval_seconds,
        )


@retry(tries=3, delay=5)
def _do_upload(file_path: str, remote_prefix: str):
    """Upload a local file to remote storage

    Parameters
    ----------
    file_path:
        The path to the local file being uploaded
    remote_prefix:
        The prefix for the remote file. The full remote path will be
        this concatenated with `/<epoch timestamp>.log`.
    """
    if remote_prefix.endswith("/"):
        remote_prefix = remote_prefix[:-1]
    remote = f"{remote_prefix}/{int(time.time() * 1000)}.log"
    S3Storage().set_from_file(remote, file_path)


def _start_log_streamer_out_of_process(
    file_path: str,
    read_from_file_descriptor: int,
    upload_interval_seconds: int,
    remote_prefix: str,
    uploader: Callable[[str, str], None],
) -> int:
    """Start a subprocess to periodically upload the log file to remote storage

    Note that the caller should always call do_upload before terminating to ensure
    that logs are not lost when the caller terminates between uploads.

    Parameters
    ----------
    file_path:
        The path to the local log file
    read_from_file_descriptor:
        The file descriptor to read from; likely the "read" end of a pipe
    upload_interval_seconds:
        The interval between uploads.
    uploader:
        An optional custom uploader for the log data

    Returns
    -------
    The process id of the process doing the logging
    """
    kwargs = dict(
        file_path=file_path,
        read_from_file_descriptor=read_from_file_descriptor,
        upload_interval_seconds=upload_interval_seconds,
        remote_prefix=remote_prefix,
        uploader=uploader,
    )
    pid = os.fork()
    if pid > 0:
        # in parent process
        return pid
    else:
        # in child process
        _stream_logs_to_remote_from_file_descriptor(**kwargs)  # type: ignore
        # can't ever reach here; the above is an infinite loop
        raise RuntimeError("This code should be unreachable!")


@contextlib.contextmanager
def ingested_logs(
    file_path: str,
    remote_prefix: str,
    upload_interval_seconds=DEFAULT_LOG_UPLOAD_INTERVAL_SECONDS,
    uploader: Optional[Callable[[str, str], None]] = None,
):
    """Code within context will have stdout/stderr (including subprocess) ingested

    The ingestion will use file_path as an on-disk cache to capture the logs to, and
    logs will be uploaded to remote storage with a storage path prefix given by
    remote_prefix.

    Parameters
    ----------
    file_path:
        The path to the local cached log file
    upload_interval_seconds:
        The amount of time between uploads
    remote_prefix:
        The prefix for the remote storage location where ingested logs live
    uploader:
        An optional override for uploading the log file.
    """
    uploader = uploader if uploader is not None else _do_upload

    pod_name = os.getenv(KUBERNETES_POD_NAME_ENV_VAR)
    if pod_name is not None:
        # print is appropriate here because we want to write to actual stdout,
        # with no logging machinary in between. This is *about* the logs. It
        # will be shown when somebody does `kubectl logs <pod name>` because
        # it will go to stdout before stdout gets redirected.
        print(
            f"To follow these logs, try:\n\t"
            f"kubectl exec -i {pod_name} -- tail -f {file_path}"
        )

    original_signal_handler = None
    streamer_pid = None

    def clean_up_streamer(signal_num, frame=None):
        if streamer_pid is not None:
            # forwarding the signal should trigger a final upload.
            # use a timeout so the parent process can still exit if
            # the child hangs for some reason (ex: during remote upload)
            _send_signal_or_kill(streamer_pid, signal_num, timeout_seconds=10)
        if original_signal_handler is not None and hasattr(
            original_signal_handler, "__call__"
        ):
            original_signal_handler(signal_num, frame)

    read_file_descriptor = None
    write_file_descriptor = None
    original_signal_handler = signal.signal(signal.SIGTERM, clean_up_streamer)
    try:
        read_file_descriptor, write_file_descriptor = os.pipe()
        os.set_blocking(read_file_descriptor, False)
        os.set_inheritable(read_file_descriptor, True)
        with redirect_to_file_descriptor(write_file_descriptor):
            streamer_pid = _start_log_streamer_out_of_process(
                file_path,
                read_file_descriptor,
                upload_interval_seconds=upload_interval_seconds,
                remote_prefix=remote_prefix,
                uploader=uploader,
            )
            try:
                yield
            except Exception:
                # make sure error is logged while logs are directed
                # for ingestion so the error gets ingested. Re-raise
                # so caller can handle/not as needed.
                traceback.print_exc()
                raise
            finally:
                signal.signal(signal.SIGTERM, original_signal_handler)
                original_signal_handler = None

                # ensure there's a final log upload, and that it contains ALL the
                # contents of stdout and stderr before we redirect them back to their
                # originals.
                sys.stdout.flush()
                sys.stderr.flush()

                clean_up_streamer(signal.SIGTERM)
    finally:
        # outermost try/finally is so we can tail logs to non-redirected stdout
        # even if the code raised an error

        # Why is this tailing useful? Because in the situations where somebody
        # is triaging some weird, complicated failure mode, it will be really helpful to
        # have quick access to the last few lines of the logs directly when looking at
        # the pod's output, without having to go to remote storage. This is ESPECIALLY
        # true when the problem is something with the "normal" logging mechanisms, like
        # a failure to upload the logs to remote. Having *some* way to see what the code
        # was doing before it died will be essential.
        _tail_log_file(file_path)
        if read_file_descriptor is not None:
            os.close(read_file_descriptor)
        if write_file_descriptor is not None:
            os.close(write_file_descriptor)


def _send_signal_or_kill(pid: int, signal_num: int, timeout_seconds: int):
    """Send the signal to the given pid. If not exited by timeout, send SIGKILL

    Parameters
    ----------
    pid:
        The pid of the process to kill
    signal_num:
        The initial signal to send. Will be sent repeatedly until the process
        terminates or the timeout occurs
    timeout_seconds:
        The maximum time to wait before sending a SIGKILL
    """
    try:
        started = time.time()
        while time.time() - started < timeout_seconds:
            os.kill(pid, signal_num)
            wait_result = os.waitpid(pid, os.WNOHANG)
            if wait_result is None:
                return
            time.sleep(0.1)
        os.kill(pid, signal.SIGKILL)
        os.waitpid(pid, 0)
    except ProcessLookupError:
        return  # process already gone


def _tail_log_file(file_path, print_func=None):
    """Print the last lines of the log file.

    The code will quickly traverse to the correct file location rather than reading
    through the whole log.
    """
    print_func = print_func if print_func is not None else print
    print_func(
        "Showing the tail of the logs for reference. For complete "
        "logs, please use the UI. This contains the last delta or two "
        "of log lines uploaded to remote storage."
    )

    print_func("\t\t.\n\t\t.\n\t\t.")  # vertical '...' to show there's truncation
    previous_path = _LAST_NON_EMPTY_DELTA_TEMPLATE.format(file_path)
    if os.path.exists(previous_path):
        with open(previous_path, "r") as fp:
            for line in fp:
                print_func(line, end="")

    with open(file_path, "r") as fp:
        for line in fp:
            print_func(line, end="")
